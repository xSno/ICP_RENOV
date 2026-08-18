from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib, json, shutil, sqlite3, tempfile, uuid, zipfile
from pathlib import Path, PurePosixPath
from ..config import BootstrapConfig, MachineConfigStore
from ..database import DatabaseService
from ..database.service import MIGRATIONS
from ..errors import ApplicationError
from ..storage import Workspace

PRODUCT_MARKER="ICP_RENOV_CONTRACTS"; FORMAT_VERSION=1; ARCHIVE_EXTENSION=".icprenovbackup"
class BackupError(ApplicationError): user_message="La sauvegarde n’a pas pu être créée. Les données de l’application sont conservées."
class RestoreError(ApplicationError): user_message="La sauvegarde ne peut pas être restaurée."
@dataclass(frozen=True)
class BackupFileEntry: path:str; size:int; sha256:str
@dataclass(frozen=True)
class BackupManifest:
    product:str; backup_format_version:int; backup_id:str; created_at_utc:str; source_schema_version:int; database_path:str; files:tuple[BackupFileEntry,...]
    def json(self): return json.dumps({**asdict(self),"files":[asdict(x) for x in self.files]},ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
    @classmethod
    def parse(cls,data):
        try:
            v=json.loads(data); r=cls(v["product"],v["backup_format_version"],v["backup_id"],v["created_at_utc"],v["source_schema_version"],v["database_path"],tuple(BackupFileEntry(**x) for x in v["files"]))
        except Exception as e: raise RestoreError("manifest") from e
        if r.product!=PRODUCT_MARKER or r.backup_format_version!=FORMAT_VERSION: raise RestoreError("format")
        return r
def _now(): return datetime.now(timezone.utc)
def _hash(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def _safe(value):
    p=PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value: raise RestoreError("path")
    return p

class BackupService:
    def __init__(self,database:DatabaseService,workspace:Workspace,config_store:MachineConfigStore,now_provider=_now):self.database=database;self.workspace=workspace;self.config_store=config_store;self.now_provider=now_provider
    def validate_destination(self,directory:Path):
        d=directory.expanduser().resolve(); root=self.workspace.root.resolve()
        if d==root or root in d.parents:raise BackupError("inside workspace")
        try:d.mkdir(parents=True,exist_ok=True); p=d/(".probe-"+uuid.uuid4().hex);p.write_bytes(b"x");p.unlink()
        except OSError as e:raise BackupError("destination") from e
        return d
    def set_destination(self,directory):
        d=self.validate_destination(directory);c=self.config_store.load();self.config_store.save(BootstrapConfig(c.active_workspace,d));return d
    def _snapshot(self,path):
        try:
            path.parent.mkdir(parents=True,exist_ok=True);path.touch(exist_ok=True)
            with self.database.connection() as src:
                dst=sqlite3.connect(str(path))
                try: src.backup(dst)
                finally: dst.close()
            db=sqlite3.connect(str(path))
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise BackupError("integrity")
            finally: db.close()
        except (OSError,sqlite3.Error) as e:raise BackupError("snapshot") from e
    def _payload(self,stage):
        root=self.workspace.root.resolve();result=[(stage/"snapshot.sqlite3",self.workspace.database_path.relative_to(root).as_posix())]
        for p in root.rglob("*"):
            if not p.is_file():continue
            rel=p.relative_to(root)
            if p==self.workspace.database_path or {"backups","tmp","logs","__pycache__"}&set(rel.parts) or p.suffix==".pyc" or p.name.endswith(("-wal","-shm")):continue
            result.append((p,rel.as_posix()))
        return result
    @staticmethod
    def validate_archive(archive):
        try:
            with zipfile.ZipFile(archive) as z:
                if z.namelist().count("manifest.json")!=1:raise RestoreError("manifest")
                m=BackupManifest.parse(z.read("manifest.json")); expected={x.path:x for x in m.files}
                if len(expected)!=len(m.files) or set(z.namelist())!={"manifest.json",*expected}:raise RestoreError("entries")
                for name,x in expected.items():
                    _safe(name);data=z.read(name)
                    if len(data)!=x.size or hashlib.sha256(data).hexdigest()!=x.sha256:raise RestoreError("hash")
                if m.database_path not in expected:raise RestoreError("database")
                with tempfile.NamedTemporaryFile(delete=False,suffix=".sqlite3") as f:f.write(z.read(m.database_path));tmp=Path(f.name)
                try:
                    db=sqlite3.connect(tmp)
                    try:
                        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RestoreError("integrity")
                    finally: db.close()
                finally:tmp.unlink(missing_ok=True)
                return m
        except (OSError,zipfile.BadZipFile,sqlite3.Error) as e:raise RestoreError("archive") from e
    def create_now(self):
        c=self.config_store.load()
        if not c.backup_directory:raise BackupError("unconfigured")
        dest=self.validate_destination(c.backup_directory);stage=dest/(".icp-backup-"+uuid.uuid4().hex);stage.mkdir()
        try:
            self._snapshot(stage/"snapshot.sqlite3");payload=self._payload(stage);entries=tuple(sorted((BackupFileEntry(rel,p.stat().st_size,_hash(p)) for p,rel in payload),key=lambda x:x.path));created=self.now_provider();m=BackupManifest(PRODUCT_MARKER,FORMAT_VERSION,uuid.uuid4().hex,created.isoformat(),self.database.schema_version(),self.workspace.database_path.relative_to(self.workspace.root).as_posix(),entries)
            temp=stage/"archive.tmp"
            with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json",m.json())
                for p,rel in payload:z.write(p,rel)
            self.validate_archive(temp);final=dest/f"ICP_RENOV_BACKUP_{created.strftime('%Y-%m-%d_%H-%M-%S')}_{m.backup_id[:8]}{ARCHIVE_EXTENSION}"
            if final.exists():raise BackupError("collision")
            temp.replace(final);return final
        except RestoreError as e:raise BackupError("validation") from e
        finally:
            try: shutil.rmtree(stage)
            except OSError: pass  # Final archive remains authoritative if post-publication cleanup fails.

class RestoreService:
    def __init__(self,config_store):self.config_store=config_store
    def restore(self,archive,target,activate=True):
        m=BackupService.validate_archive(archive)
        if m.source_schema_version>len(MIGRATIONS):raise RestoreError("future_schema","Cette sauvegarde a été créée avec une version plus récente de l’application.")
        target=Path(target).resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):raise RestoreError("nonempty")
        stage=target.parent/(target.name+".restore-"+uuid.uuid4().hex)
        try:
            stage.mkdir(parents=True)
            with zipfile.ZipFile(archive) as z:
                for e in m.files:
                    p=(stage/Path(*_safe(e.path).parts)).resolve()
                    if stage.resolve() not in p.parents:raise RestoreError("path")
                    p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(e.path))
            db=sqlite3.connect(stage/Path(*PurePosixPath(m.database_path).parts))
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RestoreError("integrity")
            finally: db.close()
            if target.exists():target.rmdir()
            stage.replace(target);workspace=Workspace(target)
            if activate:
                c=self.config_store.load();self.config_store.save(BootstrapConfig(workspace.root,c.backup_directory))
            return workspace
        except Exception:
            shutil.rmtree(stage,ignore_errors=True);raise

class RealBackupSummaryProvider:
    def __init__(self,backup,alerts=None,now_provider=_now):self.backup=backup;self.alerts=alerts;self.now_provider=now_provider
    def summary(self):
        from .contract_register import BackupSummary
        c=self.backup.config_store.load()
        if not c.backup_directory:return BackupSummary("Sauvegarde non configurée")
        try:d=self.backup.validate_destination(c.backup_directory)
        except BackupError:return BackupSummary("Le dossier de sauvegarde n’est pas accessible en écriture.")
        valid=[];bad=False
        for p in d.glob("*"+ARCHIVE_EXTENSION):
            try:valid.append((BackupService.validate_archive(p),p))
            except RestoreError:bad=True
        if not valid:return BackupSummary("Aucune sauvegarde créée","Sauvegarde à effectuer",True)
        m,p=max(valid,key=lambda x:x[0].created_at_utc);created=datetime.fromisoformat(m.created_at_utc);reminder="Une sauvegarde présente dans le dossier est illisible ou incomplète." if bad else ""
        days=self.alerts.get().backup_reminder_days if self.alerts else None
        if days is not None and self.now_provider().date()>=(created.date().fromordinal(created.date().toordinal()+days)):reminder="Sauvegarde à effectuer"
        return BackupSummary(f"Dernière sauvegarde : {created.astimezone().strftime('%d/%m/%Y %H:%M')}",reminder,True)
    def create_now(self):return self.backup.create_now()
