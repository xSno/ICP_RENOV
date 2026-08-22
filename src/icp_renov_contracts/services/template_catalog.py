from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import uuid

from ..domain import (
    AvailabilityEvaluation, AvailabilityItem, ContextAuthorization, ContractTemplate,
    ContractTemplateVersion, EXTERNAL_GATE_CODES, ExternalGateStatus,
    ReviewEvidenceStatus, TemplateDefaults, TemplateOptionCatalogs,
    TemplateValidationMetadata, TemplateVersionStatus, ValidationCheckStatus,
)
from ..documents import NonOfficialModelValidationRunner, TemplateSourceStore, TemplateStructureValidator
from ..documents.formatters import prepare_context
from ..documents.registry import FIELDS
from ..documents.validation import DocumentGenerationError
from ..errors import ContractNotFoundError, ContractPersistenceError, ContractValidationError
from ..repositories import TemplateCatalogRepository, TemplateValidationRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _external_gate_status(value: ExternalGateStatus | str) -> ExternalGateStatus:
    """Normalize the primitive status sent by UI boundaries to its domain enum."""
    if isinstance(value, ExternalGateStatus):
        return value
    if isinstance(value, str):
        try:
            return ExternalGateStatus(value)
        except ValueError as exc:
            raise ContractValidationError("external validation status") from exc
    raise ContractValidationError("external validation status")


class TemplateCatalogService:
    """Governed model/version catalog with explicit, non-legal validation evidence."""

    def __init__(self, repository: TemplateCatalogRepository,
                 source_store: TemplateSourceStore | None = None,
                 validation_repository: TemplateValidationRepository | None = None,
                 structure_validator: TemplateStructureValidator | None = None,
                 validation_runner: NonOfficialModelValidationRunner | None = None,
                 company_provider=None) -> None:
        self.repository = repository
        self.source_store = source_store
        self.validation_repository = validation_repository
        self.structure_validator = structure_validator or TemplateStructureValidator()
        self.validation_runner = validation_runner
        self.company_provider = company_provider

    def create_template(self, functional_name: str, document_kind: str = "CONTRACT",
                        contract_type_code: str = "CLIMATE_MAINTENANCE") -> ContractTemplate:
        kind=document_kind.strip()
        if not functional_name.strip() or kind not in {"CONTRACT","INTERVENTION_SHEET"}:
            raise ContractValidationError("template metadata")
        type_code=contract_type_code.strip() if kind=="CONTRACT" else ""
        if kind=="CONTRACT" and not type_code:raise ContractValidationError("template metadata")
        template = ContractTemplate(str(uuid.uuid4()), functional_name.strip(), kind, type_code, _now())
        self._persist(self.repository.create_template, template)
        return template

    def create_version(self, template: ContractTemplate, version: str, status: TemplateVersionStatus,
                       allowed_client_regimes: tuple[str, ...], validation: TemplateValidationMetadata | None = None,
                       defaults: TemplateDefaults | None = None,
                       catalogs: TemplateOptionCatalogs | None = None) -> ContractTemplateVersion:
        regimes = tuple(dict.fromkeys(allowed_client_regimes))
        if (not version.strip() or any(value not in {"CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL"} for value in regimes)
                or (template.document_kind=="INTERVENTION_SHEET" and regimes)):
            raise ContractValidationError("template version metadata")
        metadata = validation or TemplateValidationMetadata()
        if any(not isinstance(value, bool) for value in (
            metadata.requires_non_renewal_notice_days,
            metadata.requires_non_renewal_notice_channels,
            metadata.requires_breach_cure_period_days,
        )):
            raise ContractValidationError("template requiredness metadata")
        if any(regime not in regimes for regime in metadata.conclusion_required_regimes):
            raise ContractValidationError("template context metadata")
        for context in metadata.context_authorizations:
            if (context.regime not in regimes or context.conclusion_mode not in {
                    "IN_PREMISES", "OFF_PREMISES", "DISTANCE_EMAIL", "ONLINE_INTERFACE", "OTHER_DISTANCE"
                } or any(block not in {
                "BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE", "BLOCK_ELECTRONIC_TERMINATION"
            } for block in context.blocks)):
                raise ContractValidationError("template context metadata")
        option_catalogs = catalogs or TemplateOptionCatalogs()
        try:
            if any(Decimal(rate) < 0 for rate in option_catalogs.vat_rates): raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise ContractValidationError("template VAT catalog")
        for options in (option_catalogs.payment_terms, option_catalogs.payment_methods,
                        option_catalogs.non_renewal_channels, option_catalogs.early_termination_reasons):
            codes = [item.code for item in options]
            if any(not item.code.strip() or not item.label.strip() for item in options) or len(codes) != len(set(codes)):
                raise ContractValidationError("template option catalog")
        now = _now()
        item = ContractTemplateVersion(
            str(uuid.uuid4()), template.id, template.functional_name, template.contract_type_code,
            version.strip(), status, regimes, metadata, defaults or TemplateDefaults(),
            option_catalogs, now, now, document_kind=template.document_kind,
        )
        self._persist(self.repository.create_version, item)
        return item

    def add_model(self, functional_name: str, document_kind: str, version: str, source: Path,
                  target_client_regimes: tuple[str, ...] = ()) -> ContractTemplateVersion:
        self._require_governance(source_required=True)
        kind = document_kind.strip()
        targets = self._regimes(target_client_regimes)
        if kind == "INTERVENTION_SHEET" and targets:
            raise ContractValidationError("intervention target regimes")
        template_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = _now()
        template = ContractTemplate(template_id, functional_name.strip(), kind,
                                    "CLIMATE_MAINTENANCE" if kind == "CONTRACT" else "", now)
        if not template.functional_name or kind not in {"CONTRACT", "INTERVENTION_SHEET"} or not version.strip():
            raise ContractValidationError("template metadata")
        relpath = ""
        try:
            relpath, digest = self.source_store.import_source(source, version_id)
            item = ContractTemplateVersion(
                version_id, template.id, template.functional_name, template.contract_type_code,
                version.strip(), TemplateVersionStatus.TO_VALIDATE, (), TemplateValidationMetadata(),
                TemplateDefaults(), TemplateOptionCatalogs(), now, now, relpath, digest,
                document_kind=kind, target_client_regimes=targets,
            )
            self.repository.create_template_with_version(template, item)
            return self.get_version(version_id)
        except (OSError, sqlite3.Error, ValueError) as exc:
            if relpath:
                try: self.source_store.remove_import(relpath)
                except OSError: pass
            if isinstance(exc, sqlite3.Error): raise ContractPersistenceError() from exc
            if isinstance(exc, OSError): raise ContractPersistenceError() from exc
            raise ContractValidationError("DOCX source") from exc

    def create_new_version(self, previous_version_id: str, version: str, source: Path) -> ContractTemplateVersion:
        self._require_governance(source_required=True)
        previous = self.get_version(previous_version_id)
        template = self.repository.get_template(previous.template_id)
        if template is None: raise ContractNotFoundError(previous.template_id)
        version_id = str(uuid.uuid4()); now = _now(); relpath = ""
        try:
            relpath, digest = self.source_store.import_source(source, version_id)
            item = ContractTemplateVersion(
                version_id, template.id, template.functional_name, template.contract_type_code,
                version.strip(), TemplateVersionStatus.TO_VALIDATE, (), TemplateValidationMetadata(),
                TemplateDefaults(), TemplateOptionCatalogs(), now, now, relpath, digest,
                document_kind=template.document_kind, previous_version_id=previous.id,
                target_client_regimes=previous.target_client_regimes,
            )
            if not version.strip(): raise ContractValidationError("template version metadata")
            self.repository.create_version(item)
            return self.get_version(version_id)
        except (OSError, sqlite3.Error, ValueError) as exc:
            if relpath:
                try: self.source_store.remove_import(relpath)
                except OSError: pass
            if isinstance(exc, sqlite3.IntegrityError): raise ContractValidationError("version already exists") from exc
            if isinstance(exc, sqlite3.Error): raise ContractPersistenceError() from exc
            if isinstance(exc, ContractValidationError): raise
            if isinstance(exc, OSError): raise ContractPersistenceError() from exc
            raise ContractValidationError("DOCX source") from exc

    def get_version(self, version_id: str) -> ContractTemplateVersion:
        value = self.repository.get_version(version_id)
        if value is None: raise ContractNotFoundError(version_id)
        return value

    def list_compatible(self, contract_type_code: str, regime: str) -> list[ContractTemplateVersion]:
        return self.repository.list_versions(contract_type_code, regime, available_only=True)

    def list_available_intervention_sheets(self) -> list[ContractTemplateVersion]:
        return self.repository.list_versions_by_kind("INTERVENTION_SHEET", available_only=True)

    def list_all(self) -> tuple[ContractTemplateVersion, ...]:
        return tuple(self.repository.list_all_versions())

    def list_family_versions(self, template_id: str) -> tuple[ContractTemplateVersion, ...]:
        return tuple(self.repository.list_template_versions(template_id))

    def last_use(self, version_id: str) -> str | None:
        self.get_version(version_id)
        return self.repository.last_use(version_id)

    def validation_record(self, version_id: str):
        self.get_version(version_id); self._require_governance()
        return self.validation_repository.get(version_id)

    def update_status(self, version_id: str, status: TemplateVersionStatus) -> None:
        self.get_version(version_id)
        self._persist(self.repository.update_status, version_id, status, _now())

    def set_generation_metadata(self, version_id: str, source_relpath: str, source_hash: str,
                                required_company_fields: tuple[str, ...],
                                required_intervention_fields: tuple[str, ...] = ()) -> ContractTemplateVersion:
        version=self.get_version(version_id)
        fields = tuple(dict.fromkeys(value.strip() for value in required_company_fields if value.strip()))
        intervention_fields=tuple(dict.fromkeys(value.strip() for value in required_intervention_fields if value.strip()))
        allowed={"intervention.technician"}
        if any(value not in allowed for value in intervention_fields) or (version.document_kind!="INTERVENTION_SHEET" and intervention_fields):
            raise ContractValidationError("intervention requiredness metadata")
        try:
            self.repository.update_generation_metadata(version_id, source_relpath, source_hash, fields, intervention_fields, _now())
        except ValueError as exc:
            raise ContractValidationError("template version is immutable") from exc
        return self.get_version(version_id)

    def set_target_regimes(self, version_id: str, regimes: tuple[str, ...]) -> ContractTemplateVersion:
        version = self._editable(version_id)
        values = self._regimes(regimes)
        if version.document_kind != "CONTRACT" and values: raise ContractValidationError("target regimes")
        self.repository.update_target_regimes(version_id, values, _now())
        return self.get_version(version_id)

    def confirm_regime(self, version_id: str, regime: str, reference: str,
                       confirmed_at: str | None = None) -> ContractTemplateVersion:
        version = self._editable(version_id); value = regime.strip()
        if version.document_kind != "CONTRACT" or value not in self._regimes((value,)) or not reference.strip():
            raise ContractValidationError("regime confirmation")
        when = confirmed_at or date.today().isoformat()
        try: date.fromisoformat(when)
        except ValueError as exc: raise ContractValidationError("regime confirmation date") from exc
        self.validation_repository.set_regime_confirmation(version_id, value, reference.strip(), when)
        return self.get_version(version_id)

    def set_required_fields(self, version_id: str, company_fields: tuple[str, ...],
                            intervention_fields: tuple[str, ...] = ()) -> ContractTemplateVersion:
        version = self._editable(version_id)
        known = {key.split(".", 1)[1] for key in FIELDS if key.startswith("company.")}
        normalized = tuple(dict.fromkeys(value.removeprefix("company.").strip() for value in company_fields if value.strip()))
        intervention = tuple(dict.fromkeys(value.strip() for value in intervention_fields if value.strip()))
        if any(value not in known for value in normalized): raise ContractValidationError("company requiredness metadata")
        if any(value != "intervention.technician" for value in intervention): raise ContractValidationError("intervention requiredness metadata")
        if version.document_kind != "INTERVENTION_SHEET" and intervention: raise ContractValidationError("intervention requiredness metadata")
        self.repository.update_requiredness(version_id, normalized, intervention, _now())
        return self.get_version(version_id)

    def set_context_review(self, version_id: str, status: ReviewEvidenceStatus,
                           authorizations: tuple[ContextAuthorization, ...] = (),
                           conclusion_required_regimes: tuple[str, ...] = ()) -> None:
        version = self._editable(version_id)
        if version.document_kind == "INTERVENTION_SHEET":
            if status is not ReviewEvidenceStatus.NOT_APPLICABLE or authorizations or conclusion_required_regimes:
                raise ContractValidationError("sheet context is not applicable")
        elif status is ReviewEvidenceStatus.NOT_APPLICABLE:
            raise ContractValidationError("contract context review")
        regimes = set(version.allowed_client_regimes)
        for context in authorizations:
            blocks = set(context.blocks)
            if context.regime not in regimes or context.conclusion_mode not in {
                "IN_PREMISES", "OFF_PREMISES", "DISTANCE_EMAIL", "ONLINE_INTERFACE", "OTHER_DISTANCE"
            } or not blocks <= {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE", "BLOCK_ELECTRONIC_TERMINATION"}:
                raise ContractValidationError("template context metadata")
            if "BLOCK_EARLY_PERFORMANCE" in blocks and "BLOCK_WITHDRAWAL" not in blocks:
                raise ContractValidationError("early performance requires withdrawal")
        if any(value not in regimes for value in conclusion_required_regimes):
            raise ContractValidationError("template context metadata")
        self.repository.update_context_metadata(
            version_id, TemplateValidationMetadata(
                tuple(conclusion_required_regimes), tuple(authorizations),
                version.validation.requires_non_renewal_notice_days,
                version.validation.requires_non_renewal_notice_channels,
                version.validation.requires_breach_cure_period_days,
            ), _now(),
        )
        self.validation_repository.set_context_review(version_id, status, _now())

    def set_external_gate(self, version_id: str, code: str, status: ExternalGateStatus | str,
                          reference: str = "") -> None:
        self._editable(version_id)
        if code not in EXTERNAL_GATE_CODES: raise ContractValidationError("external gate")
        status = _external_gate_status(status)
        self.validation_repository.set_external_gate(version_id, code, status, reference.strip(), _now())

    def set_external_content(self, version_id: str, status: ExternalGateStatus | str, validator: str = "",
                             validation_date: str | None = None, scope: str = "", reference: str = "",
                             reservations: str = "") -> None:
        self._editable(version_id)
        status = _external_gate_status(status)
        if status is ExternalGateStatus.CONFIRMED:
            if not all(value.strip() for value in (validator, scope, reference)) or not validation_date:
                raise ContractValidationError("external content confirmation")
            try: date.fromisoformat(validation_date)
            except ValueError as exc: raise ContractValidationError("external validation date") from exc
        self.validation_repository.set_external_content(
            version_id, status, validator.strip(), validation_date, scope.strip(), reference.strip(), reservations.strip(), _now(),
        )

    def set_visual_review(self, version_id: str, status: ReviewEvidenceStatus) -> None:
        self._editable(version_id)
        if status is ReviewEvidenceStatus.CONFIRMED:
            record = self.validation_record(version_id)
            if record.render_status is not ValidationCheckStatus.PASS:
                raise ContractValidationError("visual review requires successful test")
        self.validation_repository.set_visual_review(version_id, status, _now())

    def control_structure(self, version_id: str):
        version = self._editable(version_id); self._require_governance(source_required=True)
        try: source = self.source_store.verify(version.source_relpath, version.source_hash)
        except (OSError, ValueError) as exc: raise ContractValidationError("template source integrity") from exc
        result = self.structure_validator.control(source, version.document_kind, version.contract_type_code)
        self.validation_repository.save_structure(
            version_id, ValidationCheckStatus.PASS if result.passed else ValidationCheckStatus.FAIL,
            _now(), result.issues,
        )
        return result

    def test_generation(self, version_id: str):
        version = self._editable(version_id); self._require_governance(source_required=True)
        if self.validation_runner is None: raise ContractValidationError("validation runner unavailable")
        try: source = self.source_store.verify(version.source_relpath, version.source_hash)
        except (OSError, ValueError) as exc: raise ContractValidationError("template source integrity") from exc
        tested_at = _now()
        try:
            result = self.validation_runner.run(version, source)
        except DocumentGenerationError as exc:
            if exc.code.startswith("converter_"): raise
            self.validation_repository.save_render(
                version_id, ValidationCheckStatus.FAIL, tested_at, (), ValidationCheckStatus.FAIL,
                ValidationCheckStatus.NOT_RUN, ValidationCheckStatus.FAIL, (), (), (),
            )
            raise
        self.validation_repository.save_render(
            version_id, ValidationCheckStatus.PASS, tested_at, result.cases,
            ValidationCheckStatus.PASS, ValidationCheckStatus.PASS, ValidationCheckStatus.PASS,
            result.equipment_coverage, result.evidence_paths, result.evidence_hashes,
        )
        return result

    def source_integrity(self, version_id: str) -> tuple[str, str]:
        version = self.get_version(version_id); self._require_governance(source_required=True)
        try: self.source_store.verify(version.source_relpath, version.source_hash)
        except FileNotFoundError: return "MISSING", "Fichier source introuvable"
        except ValueError: return "MISMATCH", "Le fichier source ne correspond plus à la version enregistrée"
        return "VALID", "Source conforme"

    def restore_source(self, version_id: str, selected: Path) -> Path:
        version = self.get_version(version_id); self._require_governance(source_required=True)
        if not version.source_relpath or not version.source_hash: raise ContractValidationError("template source metadata")
        if self.source_store.resolve(version.source_relpath).exists(): raise ContractValidationError("template source already present")
        try: return self.source_store.restore(selected, version.source_relpath, version.source_hash)
        except ValueError as exc: raise ContractValidationError("Ce fichier ne correspond pas à la version enregistrée. Créez une nouvelle version pour utiliser un autre document.") from exc

    def evaluate_availability(self, version_id: str) -> AvailabilityEvaluation:
        version = self.get_version(version_id); record = self.validation_record(version_id)
        source_code, _ = self.source_integrity(version_id)
        confirmations = {item.regime for item in record.regime_confirmations}
        company_missing: list[str] = []
        if self.company_provider is None:
            company_missing = list(version.required_company_fields)
        else:
            company = prepare_context({"company": self.company_provider.get()})["company"]
            company_missing = [key for key in version.required_company_fields if company.get(key) in (None, "")]
        gates = {item.code: item.status for item in record.external_gates}
        external_ok = all(gates.get(code) in {ExternalGateStatus.CONFIRMED, ExternalGateStatus.NOT_APPLICABLE} for code in EXTERNAL_GATE_CODES)
        regime_ok = version.document_kind != "CONTRACT" or bool(version.allowed_client_regimes) and set(version.allowed_client_regimes) <= confirmations
        context_ok = (record.context_review_status is ReviewEvidenceStatus.CONFIRMED if version.document_kind == "CONTRACT"
                      else record.context_review_status is ReviewEvidenceStatus.NOT_APPLICABLE)
        external_content_ok = (record.external_content_status is ExternalGateStatus.CONFIRMED
                               and bool(record.external_validator and record.external_validation_date and record.external_scope and record.external_reference))
        items = (
            AvailabilityItem("source", "Source DOCX", source_code == "VALID", "Restaurez le DOCX source conforme."),
            AvailabilityItem("structure", "Structure", record.structure_status is ValidationCheckStatus.PASS, "Contrôlez et corrigez la structure."),
            AvailabilityItem("contract_type", "Type de document", version.document_kind == "INTERVENTION_SHEET" or version.contract_type_code == "CLIMATE_MAINTENANCE", "Le type de contrat est incohérent."),
            AvailabilityItem("regime", "Régime confirmé", regime_ok, "Confirmez au moins un régime exact."),
            AvailabilityItem("company", "Données société", not company_missing, "Compléter dans Paramètres > Société : " + ", ".join(company_missing)),
            AvailabilityItem("context", "Contexte", context_ok, "Enregistrez la revue explicite de la matrice de contexte."),
            AvailabilityItem("external_gates", "Validation externe", external_ok, "Chaque contrôle externe doit être explicitement confirmé ou non applicable."),
            AvailabilityItem("docx", "Test DOCX", record.docx_status is ValidationCheckStatus.PASS, "Exécutez un test DOCX réussi."),
            AvailabilityItem("pdf", "Test PDF", record.pdf_status is ValidationCheckStatus.PASS, "Exécutez un test PDF réussi."),
            AvailabilityItem("postflight", "Contrôle post-rendu", record.postflight_status is ValidationCheckStatus.PASS, "Le contrôle post-rendu doit réussir."),
            AvailabilityItem("visual", "Contrôle visuel", record.visual_review_status in {ReviewEvidenceStatus.CONFIRMED, ReviewEvidenceStatus.NOT_APPLICABLE}, "Confirmez le contrôle visuel ou son absence d’applicabilité."),
            AvailabilityItem("external_content", "Confirmation externe du contenu", external_content_ok, "Enregistrez la confirmation externe complète du contenu."),
        )
        return AvailabilityEvaluation(version_id, items)

    def make_available(self, version_id: str) -> ContractTemplateVersion:
        version = self.get_version(version_id)
        if version.status is not TemplateVersionStatus.TO_VALIDATE: raise ContractValidationError("version is not TO_VALIDATE")
        evaluation = self.evaluate_availability(version_id)
        if not evaluation.ready: raise ContractValidationError("; ".join(evaluation.blockers))
        try: self.repository.make_available(version_id, _now())
        except ValueError as exc: raise ContractValidationError("availability changed") from exc
        return self.get_version(version_id)

    def archive(self, version_id: str) -> ContractTemplateVersion:
        self.get_version(version_id)
        try: self.repository.archive(version_id, _now())
        except ValueError as exc: raise ContractValidationError("version cannot be archived") from exc
        return self.get_version(version_id)

    def _editable(self, version_id: str) -> ContractTemplateVersion:
        version = self.get_version(version_id)
        if version.status is not TemplateVersionStatus.TO_VALIDATE or self.repository.source_in_use(version_id):
            raise ContractValidationError("template version is immutable")
        self._require_governance()
        return version

    def _require_governance(self, source_required: bool = False) -> None:
        if self.validation_repository is None: raise ContractValidationError("template validation unavailable")
        if source_required and self.source_store is None: raise ContractValidationError("template source store unavailable")

    @staticmethod
    def _regimes(regimes: tuple[str, ...]) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(value.strip() for value in regimes if value.strip()))
        if any(value not in {"CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL"} for value in values):
            raise ContractValidationError("client regime")
        return values

    @staticmethod
    def _persist(operation, *args) -> None:
        try: operation(*args)
        except LookupError as exc: raise ContractNotFoundError(str(exc)) from exc
        except sqlite3.Error as exc: raise ContractPersistenceError() from exc
