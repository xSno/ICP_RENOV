from __future__ import annotations

import shutil
import subprocess
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import ConversionError


ROOT = Path(__file__).resolve().parents[1]


def discover_libreoffice_executable() -> Path | None:
    """Discover the console launcher from PATH and normal Windows installs."""
    candidates: list[Path] = []
    for name in ("soffice.com", "soffice.exe", "soffice"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if root:
            candidates.extend([
                Path(root) / "LibreOffice" / "program" / "soffice.com",
                Path(root) / "LibreOffice" / "program" / "soffice.exe",
            ])
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def discover_word_executable() -> Path | None:
    """Bounded local discovery via App Paths, PATH, then normal Office roots."""
    candidates: list[Path] = []
    try:
        import winreg
        access_modes = [winreg.KEY_READ]
        for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
            flag = getattr(winreg, flag_name, 0)
            if flag:
                access_modes.append(winreg.KEY_READ | flag)
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in access_modes:
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE", 0, access) as key:
                        candidates.append(Path(winreg.QueryValueEx(key, "")[0]))
                except OSError:
                    pass
    except ImportError:
        pass
    on_path = shutil.which("WINWORD.EXE")
    if on_path:
        candidates.append(Path(on_path))
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if not root:
            continue
        for office_dir in ("Office16", "root/Office16", "Office15"):
            candidates.append(Path(root) / "Microsoft Office" / office_dir / "WINWORD.EXE")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


class PdfConverter(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def convert(self, docx: Path, pdf: Path) -> None: ...


class WordComConverter(PdfConverter):
    name = "microsoft-word-com"

    def __init__(self, executable: Path | None = None, attempt_log: Path | None = None):
        self.executable = executable or discover_word_executable()
        self.script = ROOT / "spike" / "tools" / "convert_word.ps1"
        self.attempt_log = attempt_log or ROOT / "spike" / "word_com_diagnostics.log"

    def available(self) -> bool:
        return bool(self.executable and self.executable.is_file() and self.script.is_file())

    def convert(self, docx: Path, pdf: Path) -> None:
        if not self.available():
            raise ConversionError("Microsoft Word COM converter unavailable")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        self.attempt_log.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
            "-InputDocx", str(docx.resolve()), "-OutputPdf", str(pdf.resolve()),
            "-AttemptLog", str(self.attempt_log.resolve()),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"Microsoft Word PDF conversion timed out after 180 seconds; diagnostics: {self.attempt_log}"
            ) from exc
        if proc.returncode != 0 or not pdf.exists():
            detail = (proc.stderr or proc.stdout or "unknown Word conversion failure").strip()
            raise ConversionError(f"Microsoft Word PDF conversion failed: {detail}")


class LibreOfficeConverter(PdfConverter):
    name = "libreoffice-headless"

    def __init__(self, executable: str | Path | None = None):
        self.executable = Path(executable) if executable else discover_libreoffice_executable()

    def available(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file())

    def convert(self, docx: Path, pdf: Path) -> None:
        if not self.available():
            raise ConversionError("LibreOffice converter unavailable")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="icp_lo_profile_") as profile_dir, tempfile.TemporaryDirectory(
            prefix="icp_lo_output_"
        ) as output_dir:
            profile_uri = Path(profile_dir).resolve().as_uri()
            command = [
                str(self.executable), f"-env:UserInstallation={profile_uri}", "--headless",
                "--convert-to", "pdf", "--outdir", output_dir, str(docx.resolve()),
            ]
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise ConversionError("LibreOffice PDF conversion timed out after 180 seconds") from exc
            generated = Path(output_dir) / f"{docx.stem}.pdf"
            if proc.returncode != 0 or not generated.is_file() or generated.stat().st_size == 0:
                detail = (proc.stderr or proc.stdout or "output PDF missing or empty").strip()
                raise ConversionError(f"LibreOffice conversion failed: {detail}")
            generated.replace(pdf)


class FailingConverter(PdfConverter):
    name = "controlled-failure"

    def available(self) -> bool:
        return True

    def convert(self, docx: Path, pdf: Path) -> None:
        raise ConversionError("Controlled PDF conversion failure")


class UnavailableConverter(PdfConverter):
    name = "unavailable"

    def available(self) -> bool:
        return False

    def convert(self, docx: Path, pdf: Path) -> None:
        raise ConversionError("PDF converter unavailable")
