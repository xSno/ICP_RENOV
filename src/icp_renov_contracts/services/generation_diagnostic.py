from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..documents.formatters import prepare_context
from ..documents.validation import DocumentGenerationError
from .capabilities import ACCEPTED_LIBREOFFICE_VERSION, DocumentCapabilityProbe


@dataclass(frozen=True)
class WorkstationGenerationStatus:
    workspace_available: bool
    docx_available: bool
    pdf_available: bool
    pdf_detail: str
    libreoffice_version: str | None
    workspace_path: Path


@dataclass(frozen=True)
class GenerationDiagnosticResult:
    succeeded: bool
    version_id: str
    docx_paths: tuple[Path, ...] = ()
    pdf_paths: tuple[Path, ...] = ()
    tested_cases: tuple[str, ...] = ()
    issue: str = ""
    output_folder: Path | None = None


class GenerationDiagnosticService:
    """Non-persistent orchestration of the production/S12 validation chain."""
    def __init__(self, workspace, workspace_service, catalog, validation_runner, capability_probe=None) -> None:
        self.workspace = workspace; self.workspace_service = workspace_service; self.catalog = catalog
        self.validation_runner = validation_runner; self.capability_probe = capability_probe or DocumentCapabilityProbe()

    def workstation_status(self) -> WorkstationGenerationStatus:
        inspection = self.workspace_service.inspect(self.workspace.root); capability = self.capability_probe.probe()
        # The production renderer is local and has no external executable prerequisite;
        # converter readiness remains the shared probe authority used by Review.
        return WorkstationGenerationStatus(inspection.available and inspection.writable, True,
            capability.pdf_available, capability.pdf_detail, capability.libreoffice_version, self.workspace.root)

    def source_state(self, version_id: str) -> tuple[str, str]: return self.catalog.source_integrity(version_id)

    def missing_company_fields(self, version_id: str) -> tuple[str, ...]:
        version = self.catalog.get_version(version_id)
        company = prepare_context({"company": self.catalog.company_provider.get()})["company"]
        return tuple(key for key in version.required_company_fields if company.get(key) in (None, ""))

    def run(self, version_id: str, *, validation_mode: bool = False) -> GenerationDiagnosticResult:
        status = self.workstation_status()
        if not status.workspace_available: return GenerationDiagnosticResult(False, version_id, issue="Le dossier de travail ne permet pas d’exécuter le test.")
        if not status.docx_available: return GenerationDiagnosticResult(False, version_id, issue="La génération DOCX est indisponible.")
        if not status.pdf_available:
            detail = f" Version détectée : {status.libreoffice_version}." if status.libreoffice_version else ""
            return GenerationDiagnosticResult(False, version_id, issue=f"Conversion PDF indisponible. Version attendue : LibreOffice {ACCEPTED_LIBREOFFICE_VERSION}.{detail}")
        source_code, source_message = self.source_state(version_id)
        if source_code != "VALID": return GenerationDiagnosticResult(False, version_id, issue=source_message)
        if self.missing_company_fields(version_id):
            return GenerationDiagnosticResult(False, version_id, issue="Des informations société requises par ce modèle sont à compléter.")
        try:
            if validation_mode:
                result = self.catalog.test_generation(version_id); root = self.workspace.root / "validation" / "models" / version_id
            else:
                version = self.catalog.get_version(version_id); source = self.catalog.source_store.verify(version.source_relpath, version.source_hash)
                # Workspace tmp is the disposable local TEMP authority available to the
                # application; it remains outside governed contract documents.
                root = self.workspace.root / "tmp" / "diagnostic"; result = self.validation_runner.run(version, source, root)
            paths = tuple(Path(item) for item in result.evidence_paths)
            return GenerationDiagnosticResult(True, version_id, tuple(p for p in paths if p.suffix.lower()==".docx"),
                tuple(p for p in paths if p.suffix.lower()==".pdf"), result.cases, output_folder=root)
        except DocumentGenerationError as exc:
            return GenerationDiagnosticResult(False, version_id, issue=getattr(exc, "user_message", "Le DOCX de test n’a pas pu être créé."))
        except (OSError, ValueError):
            return GenerationDiagnosticResult(False, version_id, issue="Le test n’a pas pu être exécuté. Les données de l’application sont conservées.")
