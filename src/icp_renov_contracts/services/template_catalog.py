from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import sqlite3
import uuid

from ..domain import (
    ContractTemplate, ContractTemplateVersion, TemplateDefaults, TemplateOptionCatalogs,
    TemplateValidationMetadata, TemplateVersionStatus,
)
from ..errors import ContractNotFoundError, ContractPersistenceError, ContractValidationError
from ..repositories import TemplateCatalogRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TemplateCatalogService:
    """Minimal metadata API; it intentionally provides no production seeding or management UI."""

    def __init__(self, repository: TemplateCatalogRepository) -> None:
        self.repository = repository

    def create_template(self, functional_name: str, document_kind: str = "CONTRACT",
                        contract_type_code: str = "CLIMATE_MAINTENANCE") -> ContractTemplate:
        if not functional_name.strip() or not document_kind.strip() or not contract_type_code.strip():
            raise ContractValidationError("template metadata")
        template = ContractTemplate(str(uuid.uuid4()), functional_name.strip(), document_kind.strip(),
                                    contract_type_code.strip(), _now())
        self._persist(self.repository.create_template, template)
        return template

    def create_version(self, template: ContractTemplate, version: str, status: TemplateVersionStatus,
                       allowed_client_regimes: tuple[str, ...], validation: TemplateValidationMetadata | None = None,
                       defaults: TemplateDefaults | None = None,
                       catalogs: TemplateOptionCatalogs | None = None) -> ContractTemplateVersion:
        regimes = tuple(dict.fromkeys(allowed_client_regimes))
        if not version.strip() or any(value not in {"CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL"} for value in regimes):
            raise ContractValidationError("template version metadata")
        metadata = validation or TemplateValidationMetadata()
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
            option_catalogs, now, now,
        )
        self._persist(self.repository.create_version, item)
        return item

    def get_version(self, version_id: str) -> ContractTemplateVersion:
        value = self.repository.get_version(version_id)
        if value is None: raise ContractNotFoundError(version_id)
        return value

    def list_compatible(self, contract_type_code: str, regime: str) -> list[ContractTemplateVersion]:
        return self.repository.list_versions(contract_type_code, regime, available_only=True)

    def update_status(self, version_id: str, status: TemplateVersionStatus) -> None:
        self.get_version(version_id)
        self._persist(self.repository.update_status, version_id, status, _now())

    def set_generation_metadata(self, version_id: str, source_relpath: str, source_hash: str,
                                required_company_fields: tuple[str, ...]) -> ContractTemplateVersion:
        self.get_version(version_id)
        fields = tuple(dict.fromkeys(value.strip() for value in required_company_fields if value.strip()))
        try:
            self.repository.update_generation_metadata(version_id, source_relpath, source_hash, fields, _now())
        except ValueError as exc:
            raise ContractValidationError("template version is immutable") from exc
        return self.get_version(version_id)

    @staticmethod
    def _persist(operation, *args) -> None:
        try: operation(*args)
        except LookupError as exc: raise ContractNotFoundError(str(exc)) from exc
        except sqlite3.Error as exc: raise ContractPersistenceError() from exc
