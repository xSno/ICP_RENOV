from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import uuid
import zipfile
from xml.etree import ElementTree as ET


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TemplateSourceStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.root = self.workspace_root / "templates" / "sources"

    def import_source(self, source: Path, version_id: str) -> tuple[str, str]:
        self.validate_docx(source)
        target_dir = self.root / version_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex}.docx"
        shutil.copyfile(source, target)
        relpath = target.relative_to(self.workspace_root).as_posix()
        return relpath, sha256_file(target)

    @staticmethod
    def validate_docx(source: Path) -> None:
        if not source.is_file(): raise FileNotFoundError(source)
        if source.suffix.lower() != ".docx" or source.stat().st_size == 0:
            raise ValueError("invalid DOCX source")
        try:
            with zipfile.ZipFile(source) as package:
                names = set(package.namelist())
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise ValueError("invalid DOCX source")
                ET.fromstring(package.read("[Content_Types].xml"))
                ET.fromstring(package.read("word/document.xml"))
        except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError("invalid DOCX source") from exc

    def resolve(self, relpath: str) -> Path:
        candidate = (self.workspace_root / Path(relpath)).resolve()
        try: candidate.relative_to(self.workspace_root)
        except ValueError as exc: raise ValueError("template source escapes workspace") from exc
        return candidate

    def verify(self, relpath: str | None, expected_hash: str | None) -> Path:
        if not relpath or not expected_hash: raise ValueError("template source metadata missing")
        source = self.resolve(relpath)
        if not source.is_file(): raise FileNotFoundError(source)
        if sha256_file(source) != expected_hash: raise ValueError("template source hash mismatch")
        return source

    def remove_import(self, relpath: str) -> None:
        target = self.resolve(relpath)
        if target.is_file(): target.unlink()
        parent = target.parent
        if parent != self.root and parent.is_dir() and not any(parent.iterdir()): parent.rmdir()

    def restore(self, source: Path, relpath: str, expected_hash: str) -> Path:
        self.validate_docx(source)
        if sha256_file(source) != expected_hash: raise ValueError("template source hash mismatch")
        target = self.resolve(relpath)
        if target.exists(): raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target
