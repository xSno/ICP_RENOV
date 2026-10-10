from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import errno, hashlib, json, logging, os, shutil, sqlite3, tempfile, uuid, zipfile, zlib
from pathlib import Path, PurePosixPath
from ..config import BootstrapConfig, MachineConfigStore
from ..database import DatabaseService
from ..database.service import MIGRATIONS
from ..errors import ApplicationError, InvalidBootstrapConfigurationError
from ..storage import Workspace

PRODUCT_MARKER="ICP_RENOV_CONTRACTS"; FORMAT_VERSION=1; ARCHIVE_EXTENSION=".icprenovbackup"
class BackupError(ApplicationError): user_message="La sauvegarde n’a pas pu être créée. Les données de l’application sont conservées."
class RestoreError(ApplicationError):
    user_message="La sauvegarde ne peut pas être restaurée."
    def __init__(self, code, user_message=None):
        self.code = code
        if user_message is not None: self.user_message = user_message
        super().__init__(code)

_logger = logging.getLogger("icp_renov_contracts")
_RESTORE_IO_ERRORS = (OSError, zipfile.BadZipFile, sqlite3.Error, zlib.error)
_RESTORE_DISK_FULL_MESSAGE = "L’espace de stockage local est insuffisant pour restaurer la sauvegarde. Libérez de l’espace puis réessayez."

def _disk_full(exc):
    return getattr(exc, "errno", None) == errno.ENOSPC or getattr(exc, "winerror", None) == 112

def _cleanup_restore(path):
    if path is None: return
    try: shutil.rmtree(path)
    except FileNotFoundError: pass
    except OSError as exc:
        _logger.warning("Restore cleanup failed; owned directory=%s type=%s errno=%s winerror=%s",
                        path, type(exc).__name__, exc.errno, getattr(exc, "winerror", None))
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

def _zip_read(z, name):
    try: return z.read(name)
    except (EOFError, RuntimeError, NotImplementedError) as exc:
        _logger.warning("Restore ZIP read failed type=%s", type(exc).__name__)
        raise RestoreError("zip_read", "La sauvegarde est illisible ou incomplète.") from exc

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
                return BackupService._validate_zip(z)
        except _RESTORE_IO_ERRORS as e:raise RestoreError("archive", "La sauvegarde est illisible ou incomplète.") from e

    @staticmethod
    def _validate_zip(z, temporary_directory=None):
        if z.namelist().count("manifest.json")!=1:raise RestoreError("manifest")
        m=BackupManifest.parse(_zip_read(z, "manifest.json"))
        if (not isinstance(m.source_schema_version, int) or isinstance(m.source_schema_version, bool)
                or not isinstance(m.database_path, str)
                or any(not isinstance(x.path, str) or not isinstance(x.size, int) or x.size < 0
                       or not isinstance(x.sha256, str) for x in m.files)):
            raise RestoreError("manifest")
        expected={x.path:x for x in m.files}
        if (len(expected)!=len(m.files) or len(z.namelist())!=len(expected)+1
                or set(z.namelist())!={"manifest.json",*expected}):raise RestoreError("entries")
        for name,x in expected.items():
            _safe(name);data=_zip_read(z, name)
            if len(data)!=x.size or hashlib.sha256(data).hexdigest()!=x.sha256:raise RestoreError("hash")
        if m.database_path not in expected:raise RestoreError("database")
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(delete=False,suffix=".sqlite3",dir=temporary_directory) as f:
                tmp=Path(f.name);f.write(_zip_read(z, m.database_path))
            db=sqlite3.connect(tmp)
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RestoreError("integrity")
            finally: db.close()
        finally:
            if tmp is not None:
                try: tmp.unlink(missing_ok=True)
                except OSError as exc:
                    _logger.warning("Restore validation temporary cleanup failed type=%s", type(exc).__name__)
        return m
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

    @staticmethod
    def _fixed_local(path):
        if os.name != "nt": return path.is_absolute()
        import ctypes
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        return bool(path.drive and not str(path).startswith("\\\\") and get_drive_type(path.anchor) == 3)

    def _source_stage(self, archive, target, active_workspace):
        candidates = []
        if os.environ.get("LOCALAPPDATA"): candidates.append(Path(os.environ["LOCALAPPDATA"]))
        candidates.extend((Path.home()/"AppData"/"Local", Path(tempfile.gettempdir())))
        excluded = [archive.parent, target]
        if active_workspace is not None: excluded.append(Path(active_workspace).resolve())
        for candidate in candidates:
            base = (candidate/"ICP Renov"/"Contrats"/"tmp"/"restore").resolve()
            if not self._fixed_local(base) or any(base == p or p in base.parents or base in p.parents for p in excluded): continue
            try:
                base.mkdir(parents=True, exist_ok=True)
                return Path(tempfile.mkdtemp(prefix=".icp-restore-source-", dir=base))
            except OSError as exc:
                _logger.warning("Restore staging candidate unavailable type=%s errno=%s winerror=%s",
                                type(exc).__name__, exc.errno, getattr(exc, "winerror", None))
                if _disk_full(exc): raise RestoreError("storage_full", _RESTORE_DISK_FULL_MESSAGE) from exc
        raise RestoreError("local_staging", "Un dossier temporaire local sûr est nécessaire pour restaurer la sauvegarde.")

    @staticmethod
    def _copy_source(archive, staged):
        # The external source is opened once, read-only, with bounded memory.
        with archive.open("rb") as source:
            expected = os.fstat(source.fileno()).st_size
            copied = 0
            while chunk := source.read(1024 * 1024):
                staged.write(chunk);copied += len(chunk)
            if copied != expected: raise RestoreError("source_truncated", "La lecture de la sauvegarde est incomplète. Copiez-la sur le poste puis réessayez.")
        staged.flush();staged.seek(0)

    def restore(self,archive,target,activate=True):
        source_stage = stage = None
        phase = "destination"
        removed_empty_target = False
        try:
            archive=Path(archive).resolve();target=Path(target).resolve()
            existed = target.exists()
            if existed and (not target.is_dir() or any(target.iterdir())):
                raise RestoreError("nonempty", "Le dossier de restauration doit être vide. Choisissez un nouveau dossier ou un dossier vide.")
            previous = self.config_store.load()
            phase = "local_staging"
            source_stage = self._source_stage(archive, target, previous.active_workspace)
            phase = "source_copy"
            with (source_stage/"archive.icprenovbackup").open("w+b") as staged:
                self._copy_source(archive, staged)
                phase = "validation"
                # Same private archive handle and ZIP instance for validation and extraction.
                with zipfile.ZipFile(staged) as z:
                    m=BackupService._validate_zip(z, source_stage)
                    if m.source_schema_version>len(MIGRATIONS):
                        raise RestoreError("future_schema","Cette sauvegarde a été créée avec une version plus récente de l’application.")
                    phase = "extraction"
                    candidate=target.parent/(target.name+".restore-"+uuid.uuid4().hex)
                    candidate.mkdir(parents=True)
                    stage=candidate  # Ownership starts only after successful creation.
                    for e in m.files:
                        p=(stage/Path(*_safe(e.path).parts)).resolve()
                        if stage.resolve() not in p.parents:raise RestoreError("path")
                        data=_zip_read(z, e.path)
                        if len(data)!=e.size or hashlib.sha256(data).hexdigest()!=e.sha256: raise RestoreError("hash")
                        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            phase = "database"
            db=sqlite3.connect(stage/Path(*PurePosixPath(m.database_path).parts))
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RestoreError("integrity")
            finally: db.close()
            phase = "promotion"
            if target.exists():
                if not existed: raise RestoreError("destination", "Le dossier de restauration n’est plus disponible.")
                target.rmdir();removed_empty_target = True
            stage.replace(target);stage=None;workspace=Workspace(target)
            if activate:
                phase = "activation"
                try: self.config_store.save(BootstrapConfig(workspace.root,previous.backup_directory))
                except (ApplicationError, OSError) as exc:
                    # MachineConfigStore saves atomically. Also recover an accepted write
                    # followed by an error, without a second activation authority.
                    if self.config_store.load() != previous:
                        try: self.config_store.save(previous)
                        except (ApplicationError, OSError):
                            if self.config_store.load() != previous:
                                _logger.critical("Restore activation rollback failed; complete target retained")
                                raise RestoreError("activation_rollback", "La configuration du poste doit être vérifiée avant de rouvrir l’application.") from exc
                    raise RestoreError("activation", "Le dossier restauré est conservé, mais n’a pas pu être activé. Le dossier courant reste inchangé.") from exc
            return workspace
        except RestoreError as exc:
            _logger.warning("Restore rejected stage=%s code=%s", phase, exc.code)
            raise
        except InvalidBootstrapConfigurationError as exc:
            _logger.warning("Restore configuration failure stage=%s type=%s", phase, type(exc).__name__)
            raise RestoreError("configuration", "La configuration du poste n’est pas accessible. La restauration n’a pas été activée.") from exc
        except _RESTORE_IO_ERRORS as exc:
            _logger.warning("Restore failed stage=%s type=%s errno=%s winerror=%s", phase,
                            type(exc).__name__, getattr(exc, "errno", None), getattr(exc, "winerror", None))
            if _disk_full(exc): message = _RESTORE_DISK_FULL_MESSAGE
            elif phase == "source_copy": message = "La sauvegarde n’est pas accessible. Copiez-la sur le poste puis réessayez."
            elif phase in ("validation", "database"): message = "La sauvegarde est illisible ou incomplète."
            elif phase == "local_staging": message = "Le dossier temporaire local n’est pas accessible."
            else: message = "La restauration a échoué. Le dossier de travail courant est conservé."
            raise RestoreError(phase, message) from exc
        finally:
            _cleanup_restore(stage);_cleanup_restore(source_stage)
            if removed_empty_target and not target.exists():
                try: target.mkdir()
                except OSError as exc: _logger.warning("Restore empty target recovery failed type=%s", type(exc).__name__)

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
