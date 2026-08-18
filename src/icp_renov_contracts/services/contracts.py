from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import sqlite3
import uuid

from ..domain import (
    ClientDraft, ClientSnapshot, ConclusionMode, Contract, ContractConditions, ContractEvent, ContractEventType, ContractStatus,
    ContractEquipmentItem, ContractRegime, DurationMode, EquipmentDraft, EquipmentSnapshot,
    INCLUDED_OPTIONS, RefrigerantHandlingMode, RenewalMode, RenewalPriceRule, SiteDraft,
    SiteSnapshot, TemplateVersionStatus,
)
from ..errors import (
    ContractConditionsValidationError, ContractNotFoundError, ContractPersistenceError,
    ContractValidationError,
)
from ..repositories import ContractConditionsRepository, ContractRepository
from .master_data import MasterDataService
from .template_catalog import TemplateCatalogService
from .settings import AlertSettingsService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContractService:
    def __init__(self, repository: ContractRepository, master_data: MasterDataService,
                 conditions_repository: ContractConditionsRepository | None = None,
                 template_catalog: TemplateCatalogService | None = None,
                 alert_settings: AlertSettingsService | None = None) -> None:
        self.repository = repository
        self.master_data = master_data
        self.conditions_repository = conditions_repository
        self.template_catalog = template_catalog
        self.alert_settings = alert_settings

    def create_draft(self) -> Contract:
        contract_id = str(uuid.uuid4()); now = _now()
        event = ContractEvent(str(uuid.uuid4()), contract_id, ContractEventType.CREATED, now)
        self._persist(self.repository.create, contract_id, now, event)
        return self.get(contract_id)

    def get(self, contract_id: str) -> Contract:
        contract = self.repository.get(contract_id)
        if contract is None: raise ContractNotFoundError(contract_id)
        return contract

    def list_drafts(self):
        return self.repository.list_drafts()

    def get_conditions(self, contract_id: str) -> ContractConditions:
        self.get(contract_id)
        if self.conditions_repository is None: return ContractConditions()
        value = self.conditions_repository.get(contract_id)
        if value is None: raise ContractNotFoundError(contract_id)
        return value

    def selected_template_version(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.template_version_id is None or self.template_catalog is None: return None
        return self.template_catalog.get_version(contract.template_version_id)

    def compatible_template_versions(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.regime is None or self.template_catalog is None: return []
        return self.template_catalog.list_compatible(contract.type_code.value, contract.regime.value)

    def _editable(self, contract_id: str) -> Contract:
        contract = self.get(contract_id)
        if contract.status is not ContractStatus.DRAFT:
            raise ContractValidationError("contract is read-only")
        return contract

    def change_regime(self, contract_id: str, regime: str | None) -> Contract:
        contract = self._editable(contract_id)
        if regime is not None and regime not in {item.value for item in ContractRegime}:
            raise ContractValidationError("invalid regime")
        if (contract.regime.value if contract.regime else None) == regime: return contract
        keep = False
        if contract.template_version_id and regime and self.template_catalog:
            version = self.template_catalog.get_version(contract.template_version_id)
            keep = version.contract_type_code == contract.type_code.value and regime in version.allowed_client_regimes
        self._persist(self.conditions_repository.change_regime, contract_id, regime, keep, _now())
        return self.get(contract_id)

    def select_template_version(self, contract_id: str, version_id: str) -> Contract:
        contract = self._editable(contract_id)
        if contract.regime is None or self.template_catalog is None:
            raise ContractValidationError("regime required")
        version = self.template_catalog.get_version(version_id)
        if (version.status is not TemplateVersionStatus.AVAILABLE or
                version.contract_type_code != contract.type_code.value or
                contract.regime.value not in version.allowed_client_regimes):
            raise ContractValidationError("incompatible template version")
        conditions = self.get_conditions(contract_id).with_empty_defaults(version.defaults)
        if (conditions.renewal_mode not in (None, RenewalMode.NONE.value)
                and conditions.internal_alert_days is None and self.alert_settings is not None):
            default_days = self.alert_settings.get().default_internal_alert_days
            if default_days is not None:
                conditions = ContractConditions(**{**asdict(conditions), "internal_alert_days": default_days})
        conditions = self._normalize_context(conditions, contract.regime.value, version, strict=False)
        conditions = self._validate_conditions(conditions, version)
        self._persist(self.conditions_repository.select_template, contract_id, version.template_id,
                      version.id, conditions, _now())
        return self.get(contract_id)

    def save_conditions(self, contract_id: str, conditions: ContractConditions) -> ContractConditions:
        contract = self._editable(contract_id)
        version = self.selected_template_version(contract_id)
        regime = contract.regime.value if contract.regime else None
        if (conditions.renewal_mode not in (None, RenewalMode.NONE.value)
                and conditions.internal_alert_days in (None, "") and self.alert_settings is not None):
            default_days = self.alert_settings.get().default_internal_alert_days
            if default_days is not None:
                conditions = ContractConditions(**{**asdict(conditions), "internal_alert_days": default_days})
        normalized = self._normalize_context(conditions, regime, version, strict=True)
        normalized = self._validate_conditions(normalized, version)
        self._persist(self.conditions_repository.save, contract_id, normalized, _now())
        return self.get_conditions(contract_id)

    @staticmethod
    def _normalize_context(conditions: ContractConditions, regime: str | None, version, strict: bool) -> ContractConditions:
        values = asdict(conditions)
        requires = bool(version and version.validation.requires_conclusion(regime))
        if not requires:
            values["conclusion_mode"] = None; values["early_performance_requested"] = None
        elif values["conclusion_mode"] is not None:
            if values["conclusion_mode"] not in {item.value for item in ConclusionMode}:
                raise ContractConditionsValidationError({"conclusion_mode": "Le mode de conclusion est invalide."})
            blocks = version.validation.blocks_for(regime, values["conclusion_mode"])
            authorized = {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(blocks)
            if not authorized:
                if strict and values["early_performance_requested"]:
                    raise ContractConditionsValidationError({"early_performance_requested": "Ce contexte n’autorise pas cette demande."})
                values["early_performance_requested"] = None
        else:
            values["early_performance_requested"] = None
        return ContractConditions(**values)

    @classmethod
    def _validate_conditions(cls, value: ContractConditions, version) -> ContractConditions:
        data = asdict(value); errors: dict[str, str] = {}
        for name in ("visits_per_year", "initial_duration_months", "payment_due_days", "renewal_period_months",
                     "non_renewal_notice_days", "internal_alert_days", "breach_cure_period_days"):
            data[name] = cls._integer(data[name], name, errors)
        if data["visits_per_year"] is not None and data["visits_per_year"] < 1:
            errors["visits_per_year"] = "Le nombre de visites doit être au moins égal à 1."
        for name in ("initial_duration_months", "renewal_period_months"):
            if data[name] is not None and data[name] < 1: errors[name] = "La durée doit être au moins égale à 1 mois."
        for name in ("payment_due_days", "non_renewal_notice_days", "internal_alert_days", "breach_cure_period_days"):
            if data[name] is not None and data[name] < 0: errors[name] = "Cette durée ne peut pas être négative."
        for name in ("issue_date", "start_date", "initial_end_date"):
            if data[name]:
                try: date.fromisoformat(data[name])
                except (TypeError, ValueError): errors[name] = "Utilisez une date valide au format AAAA-MM-JJ."
        if data["initial_duration_mode"] not in {None, *(item.value for item in DurationMode)}:
            errors["initial_duration_mode"] = "Le mode de durée est invalide."
        elif data["initial_duration_mode"] == DurationMode.STANDARD.value:
            data["initial_end_date"] = None
        elif data["initial_duration_mode"] == DurationMode.CUSTOM.value:
            data["initial_duration_months"] = None
        if data["refrigerant_handling_mode"] not in {None, *(item.value for item in RefrigerantHandlingMode)}:
            errors["refrigerant_handling_mode"] = "La gestion des fluides est invalide."
        if any(option not in INCLUDED_OPTIONS for option in data["included_options"]):
            errors["included_options"] = "Une prestation incluse est inconnue."
        data["included_options"] = tuple(dict.fromkeys(data["included_options"]))
        if data["priority_breakdown"] is not True: data["priority_breakdown_delay"] = None
        elif data["priority_breakdown_delay"] is not None and not str(data["priority_breakdown_delay"]).strip():
            data["priority_breakdown_delay"] = None
        catalogs = version.catalogs if version else None
        data["annual_ht"] = cls._money(data["annual_ht"], "annual_ht", errors)
        data["missed_appointment_fee"] = cls._money(data["missed_appointment_fee"], "missed_appointment_fee", errors)
        if data["vat_rate"] is not None:
            rate = cls._decimal(data["vat_rate"], "vat_rate", errors)
            allowed = tuple(catalogs.vat_rates) if catalogs else ()
            matched = next((item for item in allowed if rate is not None and Decimal(item) == rate), None)
            if matched is None: errors["vat_rate"] = "Choisissez un taux de TVA configuré."
            else: data["vat_rate"] = matched
        terms = {item.code: item for item in catalogs.payment_terms} if catalogs else {}
        term = terms.get(data["payment_terms_code"])
        if data["payment_terms_code"] is not None and term is None:
            errors["payment_terms_code"] = "Choisissez une modalité de paiement configurée."
        if term is None or not term.requires_day_count: data["payment_due_days"] = None
        if term is None or not term.allows_custom_text: data["payment_terms_custom_text"] = ""
        allowed_methods = {item.code for item in catalogs.payment_methods} if catalogs else set()
        if any(code not in allowed_methods for code in data["payment_methods"]):
            errors["payment_methods"] = "Un moyen de paiement est inconnu."
        data["payment_methods"] = tuple(dict.fromkeys(data["payment_methods"]))
        if data["renewal_mode"] not in {None, *(item.value for item in RenewalMode)}:
            errors["renewal_mode"] = "Le mode de renouvellement est invalide."
        if data["renewal_price_rule"] == "INDEXED" or data["renewal_price_rule"] not in {None, *(item.value for item in RenewalPriceRule)}:
            errors["renewal_price_rule"] = "La règle de prix au renouvellement est invalide."
        if data["renewal_mode"] in {None, RenewalMode.NONE.value}:
            for name, empty in (("renewal_period_months", None), ("non_renewal_notice_days", None),
                                ("non_renewal_notice_channels", ()), ("internal_alert_days", None),
                                ("renewal_price_rule", None)): data[name] = empty
        elif data["renewal_mode"] == RenewalMode.MANUAL.value:
            data["non_renewal_notice_days"] = None; data["non_renewal_notice_channels"] = ()
        allowed_channels = {item.code for item in catalogs.non_renewal_channels} if catalogs else set()
        if any(code not in allowed_channels for code in data["non_renewal_notice_channels"]):
            errors["non_renewal_notice_channels"] = "Un canal de non-renouvellement est inconnu."
        reasons = {item.code for item in catalogs.early_termination_reasons} if catalogs else set()
        if any(code not in reasons for code in data["early_termination_reason_codes"]):
            errors["early_termination_reason_codes"] = "Un motif de fin anticipée est inconnu."
        for name in ("included_area", "business_hours", "additional_exclusions", "signature_city",
                     "payment_terms_custom_text", "early_termination_custom_text", "special_terms"):
            data[name] = str(data[name]).strip() if name != "special_terms" else str(data[name])
        if errors: raise ContractConditionsValidationError(errors)
        return ContractConditions(**data)

    @staticmethod
    def _integer(value, name: str, errors: dict[str, str]):
        if value in (None, ""): return None
        try: return int(value)
        except (TypeError, ValueError): errors[name] = "Saisissez un nombre entier valide."; return None

    @staticmethod
    def _decimal(value, name: str, errors: dict[str, str]):
        if value in (None, ""): return None
        try:
            parsed = Decimal(str(value).replace(",", "."))
            if parsed < 0: raise InvalidOperation
            return parsed
        except (InvalidOperation, ValueError): errors[name] = "Saisissez un montant positif ou nul."; return None

    @classmethod
    def _money(cls, value, name: str, errors: dict[str, str]):
        parsed = cls._decimal(value, name, errors)
        return format(parsed.quantize(Decimal("0.01")), "f") if parsed is not None else None

    def selectable_clients(self, search: str = ""):
        return self.master_data.list_clients(search=search, archived=False)

    def select_client(self, contract_id: str, client_id: str) -> Contract:
        current = self._editable(contract_id)
        client = self.master_data.get_client(client_id)
        if client.archived: raise ContractValidationError("client archived")
        if current.client_source_id == client_id:
            return current
        name, role = ((client.display_name, "Client") if client.party_type == "PERSON" else
                      (client.proposed_contact_name, client.proposed_contact_role))
        self._persist(self.repository.select_client, contract_id, client.id,
                      ClientSnapshot.from_master(client), name, role, _now())
        return self.get(contract_id)

    def create_and_select_client(self, contract_id: str, draft: ClientDraft) -> Contract:
        self._editable(contract_id)
        return self.select_client(contract_id, self.master_data.create_client(draft).id)

    def selectable_sites(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.client_source_id is None: return []
        return [site for site in self.master_data.list_sites(contract.client_source_id) if not site.archived]

    def select_site(self, contract_id: str, site_id: str) -> Contract:
        current = self._editable(contract_id)
        if current.client_source_id is None: raise ContractValidationError("client required")
        site = self.master_data.get_site(site_id)
        if site.archived or site.client_id != current.client_source_id:
            raise ContractValidationError("site outside selected client")
        if current.site_source_id == site_id:
            return current
        self._persist(self.repository.select_site, contract_id, site.id, SiteSnapshot.from_master(site), _now())
        return self.get(contract_id)

    def create_and_select_site(self, contract_id: str, draft: SiteDraft) -> Contract:
        contract = self._editable(contract_id)
        if contract.client_source_id is None: raise ContractValidationError("client required")
        return self.select_site(contract_id, self.master_data.create_site(contract.client_source_id, draft).id)

    def selectable_equipment(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.site_source_id is None: return []
        return [item for item in self.master_data.list_equipment(contract.site_source_id) if not item.archived]

    def select_equipment(self, contract_id: str, equipment_id: str) -> Contract:
        contract = self._editable(contract_id)
        if contract.site_source_id is None: raise ContractValidationError("site required")
        if any(item.source_equipment_id == equipment_id for item in contract.equipment_items):
            return contract
        equipment = self.master_data.get_equipment(equipment_id)
        if equipment.archived or equipment.site_id != contract.site_source_id:
            raise ContractValidationError("equipment outside selected site")
        item = ContractEquipmentItem(
            id=str(uuid.uuid4()), contract_id=contract_id, source_equipment_id=equipment.id,
            position=len(contract.equipment_items), snapshot=EquipmentSnapshot.from_master(equipment),
        )
        self._persist(self.repository.add_equipment, item, _now())
        return self.get(contract_id)

    def create_and_select_equipment(self, contract_id: str, draft: EquipmentDraft) -> Contract:
        contract = self._editable(contract_id)
        if contract.site_source_id is None: raise ContractValidationError("site required")
        equipment = self.master_data.create_equipment(contract.site_source_id, draft)
        return self.select_equipment(contract_id, equipment.id)

    def deselect_equipment(self, contract_id: str, equipment_id: str) -> Contract:
        contract = self._editable(contract_id)
        item = next((value for value in contract.equipment_items if value.source_equipment_id == equipment_id), None)
        if item is None: raise ContractValidationError("equipment not selected")
        remaining = [value.id for value in contract.equipment_items if value.id != item.id]
        self._persist(self.repository.remove_equipment, contract_id, item.id, remaining, _now())
        return self.get(contract_id)

    def move_equipment(self, contract_id: str, item_id: str, delta: int) -> Contract:
        if delta not in {-1, 1}: raise ContractValidationError("invalid movement")
        contract = self._editable(contract_id)
        ids = [item.id for item in contract.equipment_items]
        if item_id not in ids: raise ContractValidationError("item not selected")
        index = ids.index(item_id)
        target = index + delta
        if target < 0 or target >= len(ids): return contract
        ids[index], ids[target] = ids[target], ids[index]
        self._persist(self.repository.reorder, contract_id, ids, _now())
        return self.get(contract_id)

    def update_observation(self, contract_id: str, item_id: str, observation: str) -> Contract:
        contract = self._editable(contract_id)
        if item_id not in {item.id for item in contract.equipment_items}:
            raise ContractValidationError("item not selected")
        self._persist(self.repository.update_observation, contract_id, item_id, observation.strip(), _now())
        return self.get(contract_id)

    def update_signatory(self, contract_id: str, name: str, role: str) -> Contract:
        self._editable(contract_id)
        self._persist(self.repository.update_signatory, contract_id, name.strip(), role.strip(), _now())
        return self.get(contract_id)

    @staticmethod
    def _persist(operation, *args) -> None:
        try:
            operation(*args)
        except LookupError as exc:
            raise ContractNotFoundError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise ContractPersistenceError() from exc
