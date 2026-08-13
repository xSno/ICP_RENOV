from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..errors import (
    NetworkWorkspaceRejectedError,
    WorkspaceNotWritableError,
    WorkspaceUnavailableError,
)


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "icp-renov.sqlite3"

    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"


@dataclass(frozen=True)
class WorkspaceInspection:
    path: Path
    available: bool
    writable: bool
    error: type[Exception] | None = None


class WorkspaceService:
    @staticmethod
    def default_workspace(documents_dir: Path | None = None) -> Path:
        documents = documents_dir or Path.home() / "Documents"
        return documents / "ICP Renov" / "Contrats"

    @staticmethod
    def _is_obvious_network_path(path: Path) -> bool:
        raw = str(path).strip().replace("/", "\\")
        return raw.startswith("\\\\")

    def validate_candidate(self, path: Path) -> Path:
        candidate = Path(path).expanduser()
        if self._is_obvious_network_path(candidate):
            raise NetworkWorkspaceRejectedError(str(candidate))
        if candidate.exists() and not candidate.is_dir():
            raise WorkspaceUnavailableError(str(candidate))
        return candidate

    def inspect(self, path: Path) -> WorkspaceInspection:
        try:
            candidate = self.validate_candidate(path)
        except NetworkWorkspaceRejectedError:
            return WorkspaceInspection(Path(path), False, False, NetworkWorkspaceRejectedError)
        except WorkspaceUnavailableError:
            return WorkspaceInspection(Path(path), False, False, WorkspaceUnavailableError)
        if not candidate.exists():
            return WorkspaceInspection(candidate, False, False)
        return WorkspaceInspection(candidate, True, os.access(candidate, os.W_OK))

    def ensure(self, path: Path) -> Workspace:
        candidate = self.validate_candidate(path)
        workspace = Workspace(candidate)
        try:
            for directory in (
                workspace.root,
                workspace.data_dir,
                workspace.documents_dir,
                workspace.backups_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceUnavailableError(str(candidate)) from exc

        probe = workspace.root / f".write-probe-{uuid.uuid4().hex}"
        try:
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
        except OSError as exc:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceNotWritableError(str(candidate)) from exc
        return workspace

