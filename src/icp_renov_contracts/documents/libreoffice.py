from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


QUALIFIED_LIBREOFFICE_VERSIONS = frozenset({"26.2.5.2"})


class LibreOfficeState(Enum):
    ABSENT = "ABSENT"
    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    PROBE_FAILED = "PROBE_FAILED"


@dataclass(frozen=True)
class LibreOfficeRuntimeStatus:
    state: LibreOfficeState
    executable: Path | None = None
    version: str | None = None
    version_output: str = ""


def _windows_background_process_kwargs() -> dict[str, int]:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def discover_libreoffice_executable() -> Path | None:
    candidates: list[Path] = []
    for name in ("soffice.com", "soffice.exe", "soffice"):
        if found := shutil.which(name):
            candidates.append(Path(found))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if root := os.environ.get(variable):
            candidates.extend((Path(root) / "LibreOffice/program/soffice.com",
                               Path(root) / "LibreOffice/program/soffice.exe"))
    return next((item.resolve() for item in candidates if item.is_file()), None)


class LibreOfficeRuntimeProbe:
    """One short-lived, immutable inspection shared by all production consumers."""

    def __init__(self, executable: Path | None = None, *, cache_seconds: float = 5.0) -> None:
        self._explicit_executable = executable
        self.cache_seconds = cache_seconds
        self._cached: LibreOfficeRuntimeStatus | None = None
        self._checked_at: float | None = None

    def inspect(self, refresh: bool = False) -> LibreOfficeRuntimeStatus:
        if (not refresh and self._cached is not None and self._checked_at is not None
                and time.monotonic() - self._checked_at < self.cache_seconds):
            return self._cached
        executable = self._explicit_executable or discover_libreoffice_executable()
        if executable is None or (self._explicit_executable is not None and not executable.is_file()):
            status = LibreOfficeRuntimeStatus(LibreOfficeState.ABSENT)
        else:
            executable = executable.resolve()
            try:
                result = subprocess.run(
                    [str(executable), "--version"], capture_output=True, text=True,
                    timeout=15, check=False, **_windows_background_process_kwargs(),
                )
            except (OSError, subprocess.SubprocessError, UnicodeError):
                status = LibreOfficeRuntimeStatus(LibreOfficeState.PROBE_FAILED, executable)
            else:
                output = result.stdout + result.stderr
                versions = re.findall(r"\bLibreOffice\s+(\d+\.\d+\.\d+\.\d+)(?![\w.])", output)
                if result.returncode != 0 or len(versions) != 1:
                    status = LibreOfficeRuntimeStatus(LibreOfficeState.PROBE_FAILED, executable,
                                                     version_output=output)
                else:
                    version = versions[0]
                    state = (LibreOfficeState.QUALIFIED if version in QUALIFIED_LIBREOFFICE_VERSIONS
                             else LibreOfficeState.UNQUALIFIED)
                    status = LibreOfficeRuntimeStatus(state, executable, version, output)
        self._cached = status
        self._checked_at = time.monotonic()
        return status

    def refresh(self) -> LibreOfficeRuntimeStatus:
        return self.inspect(refresh=True)
