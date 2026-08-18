from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import hashlib, re, shutil, uuid
from decimal import Decimal, InvalidOperation
from ..domain import CompanySettings
from ..repositories import CompanySettingsRepository, TemplateCatalogRepository

class CompanySettingsService:
    def __init__(self, repository: CompanySettingsRepository, templates: TemplateCatalogRepository, workspace_root: Path) -> None:
        self.repository=repository; self.templates=templates; self.workspace_root=workspace_root.resolve()
    def get(self) -> CompanySettings: return self.repository.get()
    def save(self, settings: CompanySettings) -> CompanySettings:
        values=settings.__dict__.copy()
        for key,value in tuple(values.items()):
            if isinstance(value,str): values[key]=value.strip()
        try:
            capital = values["share_capital"]
            values["share_capital"] = None if capital in (None, "") else Decimal(str(capital).replace(" ", "").replace(",", "."))
        except (InvalidOperation, ValueError): raise ValueError("share_capital")
        values["siren"]=re.sub(r"\s+", "", values["siren"]); values["siret"]=re.sub(r"\s+", "", values["siret"]); values["ape_code"]=values["ape_code"].upper()
        if values["siren"] and not re.fullmatch(r"\d{9}", values["siren"]): raise ValueError("siren")
        if values["siret"] and not re.fullmatch(r"\d{14}", values["siret"]): raise ValueError("siret")
        if values["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",values["email"]): raise ValueError("email")
        if values["mediator_website"] and not re.fullmatch(r"https?://[^\s]+",values["mediator_website"]): raise ValueError("url")
        if any(len(str(value))>1000 for value in values.values() if value is not None): raise ValueError("length")
        self.repository.save(CompanySettings(**values)); return self.get()
    def required_fields(self) -> set[str]:
        versions = self.templates.list_versions_by_kind("CONTRACT", False) + self.templates.list_versions_by_kind("INTERVENTION_SHEET", False)
        return {field for version in versions if version.status.value != "ARCHIVED" for field in version.required_company_fields}
    def logo_integrity(self) -> bool:
        item=self.get(); path=(self.workspace_root/item.logo_relpath).resolve() if item.logo_relpath else None
        try:return path is None or (path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item.logo_hash)
        except OSError:return False
    def import_logo(self, source: Path) -> CompanySettings:
        data=source.read_bytes(); suffix=source.suffix.lower()
        if not data or suffix not in {".png",".jpg",".jpeg"} or not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")): raise ValueError("logo")
        destination=self.workspace_root/"settings"/"company"/"logo"/(uuid.uuid4().hex+suffix); destination.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,destination)
        try:return self.save(replace(self.get(),logo_relpath=destination.relative_to(self.workspace_root).as_posix(),logo_hash=hashlib.sha256(data).hexdigest()))
        except Exception: destination.unlink(missing_ok=True); raise
    def remove_logo(self) -> CompanySettings:
        current=self.get(); saved=self.save(replace(current,logo_relpath=None,logo_hash=None))
        if current.logo_relpath:
            path=(self.workspace_root/current.logo_relpath).resolve(); logo_root=(self.workspace_root/"settings"/"company"/"logo").resolve()
            if logo_root in path.parents:
                try: path.unlink(missing_ok=True)
                except OSError: pass
        return saved
