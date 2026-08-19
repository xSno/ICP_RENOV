from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess


ACCEPTED_LIBREOFFICE_VERSION = "26.2.5.2"


@dataclass(frozen=True)
class DocumentCapabilities:
    docx_available: bool
    pdf_available: bool
    pdf_detail: str
    libreoffice_version: str | None = None
    executable: Path | None = None


class DocumentCapabilityProbe:
    """Non-rendering production capability probe. It never imports or calls the frozen spike."""

    def probe(self) -> DocumentCapabilities:
        executable = self._find_soffice()
        if executable is None:
            return DocumentCapabilities(False, False, "Conversion PDF indisponible")
        try:
            result = subprocess.run(
                [str(executable), "--version"], capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return DocumentCapabilities(False, False, "Conversion PDF à vérifier")
        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        version = match.group(1) if match else None
        accepted = result.returncode == 0 and version == ACCEPTED_LIBREOFFICE_VERSION
        return DocumentCapabilities(
            False, accepted, "Conversion PDF disponible" if accepted else "Conversion PDF à vérifier", version, executable
        )

    @staticmethod
    def _find_soffice() -> Path | None:
        on_path = shutil.which("soffice.com") or shutil.which("soffice.exe") or shutil.which("soffice")
        candidates = [on_path]
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(variable)
            if base:
                candidates.extend((str(Path(base) / "LibreOffice" / "program" / "soffice.com"),
                                   str(Path(base) / "LibreOffice" / "program" / "soffice.exe")))
        for candidate in candidates:
            if candidate and Path(candidate).is_file(): return Path(candidate)
        return None
