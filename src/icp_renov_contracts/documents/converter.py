from __future__ import annotations

from abc import ABC, abstractmethod
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from .validation import DocumentGenerationError
from .libreoffice import (
    LibreOfficeRuntimeProbe, LibreOfficeState, _windows_background_process_kwargs,
    discover_libreoffice_executable,
)


class PdfConverter(ABC):
    @abstractmethod
    def available(self) -> bool: ...
    @abstractmethod
    def convert(self, docx: Path, pdf: Path) -> None: ...


class LibreOfficeConverter(PdfConverter):
    def __init__(self, executable: Path | None = None, timeout_seconds: int = 180,
                 *, runtime: LibreOfficeRuntimeProbe | None = None) -> None:
        if runtime is not None and executable is not None:
            raise ValueError("Specify the shared runtime or an executable, not both")
        self.runtime = runtime if runtime is not None else LibreOfficeRuntimeProbe(executable)
        self.timeout_seconds = timeout_seconds

    @property
    def executable(self) -> Path | None:
        return self.runtime.inspect().executable

    def version(self, refresh: bool = False) -> str | None:
        return self.runtime.inspect(refresh=refresh).version

    def available(self) -> bool:
        return self.runtime.inspect().state is LibreOfficeState.QUALIFIED

    def convert(self, docx: Path, pdf: Path) -> None:
        status = self.runtime.inspect()
        if status.state is LibreOfficeState.ABSENT:
            raise DocumentGenerationError("converter_unavailable", "Le PDF n’a pas pu être créé.")
        if status.state is not LibreOfficeState.QUALIFIED:
            raise DocumentGenerationError("converter_version", "La conversion PDF n’est pas disponible avec la version installée.")
        self._convert(docx, pdf, status.executable)

    def convert_candidate(self, docx: Path, pdf: Path) -> None:
        """Non-official compatibility evidence only; never changes qualification."""
        status = self.runtime.inspect()
        if status.state not in {LibreOfficeState.QUALIFIED, LibreOfficeState.UNQUALIFIED}:
            raise DocumentGenerationError("converter_unavailable", "La conversion PDF de test est indisponible.")
        self._convert(docx, pdf, status.executable)

    def _convert(self, docx: Path, pdf: Path, executable: Path) -> None:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        profile_dir = tempfile.mkdtemp(prefix="icp_lo_profile_")
        output_dir = None
        try:
            output_dir = docx.parent / f"lo_output_{uuid.uuid4().hex}"
            output_dir.mkdir()
            command = [str(executable), f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
                       "--headless", "--norestore", "--nodefault", "--nolockcheck",
                       "--convert-to", "pdf", "--outdir", str(output_dir), str(docx.resolve())]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       **_windows_background_process_kwargs())
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, **_windows_background_process_kwargs())
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
        finally:
            if output_dir is not None:
                shutil.rmtree(output_dir, ignore_errors=True)
            shutil.rmtree(profile_dir, ignore_errors=True)
