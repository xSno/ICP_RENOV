from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..documents.libreoffice import LibreOfficeRuntimeProbe, LibreOfficeState


@dataclass(frozen=True)
class DocumentCapabilities:
    docx_available: bool
    pdf_available: bool
    pdf_detail: str
    libreoffice_version: str | None = None
    executable: Path | None = None
    libreoffice_state: LibreOfficeState | None = None
    version_output: str = ""


class DocumentCapabilityProbe:
    """Thin, non-rendering projection of the shared production runtime authority."""

    def __init__(self, runtime: LibreOfficeRuntimeProbe | None = None) -> None:
        self.runtime = runtime if runtime is not None else LibreOfficeRuntimeProbe()

    def probe(self, refresh: bool = False) -> DocumentCapabilities:
        status = self.runtime.inspect(refresh=refresh)
        details = {
            LibreOfficeState.ABSENT: "Conversion PDF indisponible · LibreOffice absent",
            LibreOfficeState.QUALIFIED: f"Conversion PDF disponible · LibreOffice {status.version} pris en charge",
            LibreOfficeState.UNQUALIFIED: f"Conversion PDF indisponible · LibreOffice {status.version} non qualifié",
            LibreOfficeState.PROBE_FAILED: "Conversion PDF à vérifier",
        }
        return DocumentCapabilities(
            True, status.state is LibreOfficeState.QUALIFIED, details[status.state],
            status.version, status.executable, status.state, status.version_output,
        )
