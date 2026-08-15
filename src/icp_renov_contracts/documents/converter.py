from __future__ import annotations

from abc import ABC, abstractmethod
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

from .validation import DocumentGenerationError


REQUIRED_LIBREOFFICE_VERSION = "26.2.5.2"


def discover_libreoffice_executable() -> Path | None:
    candidates: list[Path] = []
    for name in ("soffice.com", "soffice.exe", "soffice"):
        found = shutil.which(name)
        if found: candidates.append(Path(found))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if root := os.environ.get(variable):
            candidates.extend((Path(root) / "LibreOffice/program/soffice.com", Path(root) / "LibreOffice/program/soffice.exe"))
    return next((item.resolve() for item in candidates if item.is_file()), None)


class PdfConverter(ABC):
    @abstractmethod
    def available(self) -> bool: ...
    @abstractmethod
    def convert(self, docx: Path, pdf: Path) -> None: ...


class LibreOfficeConverter(PdfConverter):
    def __init__(self, executable: Path | None = None, timeout_seconds: int = 180) -> None:
        self.executable = executable or discover_libreoffice_executable()
        self.timeout_seconds = timeout_seconds

    def version(self) -> str | None:
        if not self.executable: return None
        try:
            result = subprocess.run([str(self.executable), "--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError): return None
        match = re.search(r"LibreOffice\s+([0-9.]+)", result.stdout + result.stderr)
        return match.group(1) if result.returncode == 0 and match else None

    def available(self) -> bool:
        return bool(self.executable and self.executable.is_file() and self.version() == REQUIRED_LIBREOFFICE_VERSION)

    def convert(self, docx: Path, pdf: Path) -> None:
        if not self.executable or not self.executable.is_file():
            raise DocumentGenerationError("converter_unavailable", "Le PDF n’a pas pu être créé.")
        if self.version() != REQUIRED_LIBREOFFICE_VERSION:
            raise DocumentGenerationError("converter_version", "La conversion PDF n’est pas disponible avec la version installée.")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        profile_dir = tempfile.mkdtemp(prefix="icp_lo_profile_")
        try:
            output_dir = docx.parent / f"lo_output_{uuid.uuid4().hex}"
            output_dir.mkdir()
            command = [str(self.executable), f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
                       "--headless", "--norestore", "--nodefault", "--nolockcheck",
                       "--convert-to", "pdf", "--outdir", str(output_dir), str(docx.resolve())]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else: process.kill()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                raise DocumentGenerationError("converter_timeout", "Le PDF n’a pas pu être créé.") from exc
            generated = output_dir / f"{docx.stem}.pdf"
            try:
                if process.returncode != 0 or not generated.is_file() or generated.stat().st_size == 0:
                    raise DocumentGenerationError("converter_failure", "Le PDF n’a pas pu être créé.")
                generated.replace(pdf)
            finally:
                if generated.exists(): generated.unlink()
                shutil.rmtree(output_dir, ignore_errors=True)
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
