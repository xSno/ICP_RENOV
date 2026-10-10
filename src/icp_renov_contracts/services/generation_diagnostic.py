from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..documents.formatters import prepare_context
from ..documents.validation import DocumentGenerationError
from .capabilities import DocumentCapabilityProbe
from ..documents.libreoffice import LibreOfficeState, QUALIFIED_LIBREOFFICE_VERSIONS


@dataclass(frozen=True)
class WorkstationGenerationStatus:
    workspace_available: bool
    docx_available: bool
    pdf_available: bool
    pdf_detail: str
    libreoffice_version: str | None
    workspace_path: Path
    libreoffice_state: LibreOfficeState = LibreOfficeState.ABSENT
    executable: Path | None = None
    version_output: str = ""


@dataclass(frozen=True)
class GenerationDiagnosticResult:
    succeeded: bool
    version_id: str
    docx_paths: tuple[Path, ...] = ()
    pdf_paths: tuple[Path, ...] = ()
    tested_cases: tuple[str, ...] = ()
    issue: str = ""
    output_folder: Path | None = None
    libreoffice_version: str | None = None
    executable: Path | None = None
    libreoffice_state: LibreOfficeState = LibreOfficeState.ABSENT
    version_output: str = ""
    compatibility_candidate: bool = False


class GenerationDiagnosticService:
    """Non-persistent orchestration of the production/S12 validation chain."""
    def __init__(self, workspace, workspace_service, catalog, validation_runner, capability_probe=None) -> None:
        self.workspace = workspace; self.workspace_service = workspace_service; self.catalog = catalog
        self.validation_runner = validation_runner; self.capability_probe = capability_probe or DocumentCapabilityProbe()

    def workstation_status(self) -> WorkstationGenerationStatus:
        inspection = self.workspace_service.inspect(self.workspace.root)
        capability = self.capability_probe.probe(refresh=True) if isinstance(self.capability_probe, DocumentCapabilityProbe) else self.capability_probe.probe()
        state = capability.libreoffice_state
        if state is None:  # Backwards-compatible test adapters; production always supplies explicit state.
            state = (LibreOfficeState.QUALIFIED if capability.pdf_available else
                     LibreOfficeState.UNQUALIFIED if capability.libreoffice_version else LibreOfficeState.ABSENT)
        # The production renderer is local and has no external executable prerequisite;
        # converter readiness remains the shared probe authority used by Review.
        return WorkstationGenerationStatus(inspection.available and inspection.writable, True,
            capability.pdf_available, capability.pdf_detail, capability.libreoffice_version, self.workspace.root,
            state, capability.executable, capability.version_output)

    def source_state(self, version_id: str) -> tuple[str, str]: return self.catalog.source_integrity(version_id)

    def missing_company_fields(self, version_id: str) -> tuple[str, ...]:
        version = self.catalog.get_version(version_id)
        company = prepare_context({"company": self.catalog.company_provider.get()})["company"]
        return tuple(key for key in version.required_company_fields if company.get(key) in (None, ""))

    def run(self, version_id: str, *, validation_mode: bool = False) -> GenerationDiagnosticResult:
        status = self.workstation_status()
        candidate = status.libreoffice_state is LibreOfficeState.UNQUALIFIED and not validation_mode
        evidence = dict(libreoffice_version=status.libreoffice_version, executable=status.executable,
                        libreoffice_state=status.libreoffice_state, version_output=status.version_output,
                        compatibility_candidate=False)
        if not status.workspace_available: return GenerationDiagnosticResult(False, version_id, issue="Le dossier de travail ne permet pas d’exécuter le test.", **evidence)
        if not status.docx_available: return GenerationDiagnosticResult(False, version_id, issue="La génération DOCX est indisponible.", **evidence)
        if status.libreoffice_state is LibreOfficeState.ABSENT:
            return GenerationDiagnosticResult(False, version_id, issue="Conversion PDF indisponible : LibreOffice absent.", **evidence)
        if status.libreoffice_state is LibreOfficeState.PROBE_FAILED:
            return GenerationDiagnosticResult(False, version_id, issue="LibreOffice détecté mais version impossible à vérifier.", **evidence)
        if not status.pdf_available and not candidate:
            qualified = ', '.join(sorted(QUALIFIED_LIBREOFFICE_VERSIONS))
            return GenerationDiagnosticResult(False, version_id, issue=f"Conversion PDF officielle indisponible. Version attendue : LibreOffice {qualified}. Version détectée : {status.libreoffice_version}.", **evidence)
        source_code, source_message = self.source_state(version_id)
        if source_code != "VALID": return GenerationDiagnosticResult(False, version_id, issue=source_message, **evidence)
        if self.missing_company_fields(version_id):
            return GenerationDiagnosticResult(False, version_id, issue="Des informations société requises par ce modèle sont à compléter.", **evidence)
        try:
            if validation_mode:
                result = self.catalog.test_generation(version_id); root = self.workspace.root / "validation" / "models" / version_id
            else:
                version = self.catalog.get_version(version_id); source = self.catalog.source_store.verify(version.source_relpath, version.source_hash)
                # Workspace tmp is the disposable local TEMP authority available to the
                # application; it remains outside governed contract documents.
                root = self.workspace.root / "tmp" / "diagnostic"
                if candidate:
                    evidence["compatibility_candidate"] = True
                    result = self.validation_runner.run(version, source, root, compatibility_candidate=True)
                else:
                    result = self.validation_runner.run(version, source, root)
            paths = tuple(Path(item) for item in result.evidence_paths)
            return GenerationDiagnosticResult(True, version_id, tuple(p for p in paths if p.suffix.lower()==".docx"),
                tuple(p for p in paths if p.suffix.lower()==".pdf"), result.cases, output_folder=root, **evidence)
        except DocumentGenerationError as exc:
            issue = getattr(exc, "user_message", "Le DOCX de test n’a pas pu être créé.")
            if candidate: issue = "Le test local de compatibilité a échoué. " + issue
            return GenerationDiagnosticResult(False, version_id, issue=issue, **evidence)
        except (OSError, ValueError):
            issue = "Le test n’a pas pu être exécuté. Les données de l’application sont conservées."
            if candidate: issue = "Le test local de compatibilité a échoué. " + issue
            return GenerationDiagnosticResult(False, version_id, issue=issue, **evidence)
