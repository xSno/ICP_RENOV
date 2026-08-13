from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import uuid


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
        if not source.is_file(): raise FileNotFoundError(source)
        target_dir = self.root / version_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex}.docx"
        shutil.copyfile(source, target)
        relpath = target.relative_to(self.workspace_root).as_posix()
        return relpath, sha256_file(target)

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
