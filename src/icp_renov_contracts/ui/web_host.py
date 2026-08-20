from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..domain import (
    ClientDraft, ClientMaster, ContractConditions, ContractStatus, EquipmentDraft, EquipmentMaster,
    SiteDraft, SiteMaster, TemplateVersionStatus,
)
from ..errors import ApplicationError, ContractConditionsValidationError, MasterDataValidationError
from ..services import (
    ContractOperationalSignalKind,
    ContractRegisterFilter,
    ContractRegisterService,
    RealBackupSummaryProvider,
)
from ..services.contract_register import STATUS_LABELS


CLIENT_DTO_FIELDS = frozenset({
    "party_type", "first_name", "last_name", "organization_name", "legal_form", "siret",
    "address_line1", "address_line2", "postal_code", "city", "country", "billing_address",
    "phone", "email", "internal_reference", "internal_notes",
})
SITE_DTO_FIELDS = frozenset({
    "label", "address_line1", "address_line2", "postal_code", "city", "country",
    "contact_name", "contact_phone", "internal_notes",
})
EQUIPMENT_DTO_FIELDS = frozenset({
    "equipment_type", "brand", "model", "serial_number", "power_kw", "location",
    "installation_date", "internal_reference", "internal_notes",
})
CONTRACT_FRAMEWORK_DTO_FIELDS = frozenset({
    "regime", "conclusion_mode", "early_performance_requested",
})
CONTRACT_SERVICE_DTO_FIELDS = frozenset({
    "visits_per_year", "refrigerant_handling_mode", "included_options",
    "priority_breakdown", "priority_breakdown_delay",
})
CONTRACT_PERIOD_DTO_FIELDS = frozenset({
    "issue_date", "start_date", "initial_duration_mode", "initial_duration_months",
    "initial_end_date", "signature_city",
})
CONTRACT_INTERVENTION_DTO_FIELDS = frozenset({
    "included_area", "business_hours", "travel_included",
    "missed_appointment_fee", "additional_exclusions",
})
CONTRACT_PRICING_DTO_FIELDS = frozenset({
    "annual_ht", "vat_rate", "payment_terms_code", "payment_due_days",
    "payment_terms_custom_text", "payment_methods",
})


def _client_editor_payload(client: ClientMaster) -> dict[str, str]:
    return {field: getattr(client, field) for field in CLIENT_DTO_FIELDS}


def _client_draft(payload: object, current: ClientMaster | None = None) -> ClientDraft:
    if not isinstance(payload, dict):
        raise MasterDataValidationError({"payload": "Les informations du client sont invalides."})
    unsupported = set(payload) - CLIENT_DTO_FIELDS
    if unsupported:
        raise MasterDataValidationError({
            "payload": "Certaines informations ne sont pas autorisées dans la fiche client."
        })
    invalid = [field for field, value in payload.items() if not isinstance(value, str)]
    if invalid:
        raise MasterDataValidationError({
            invalid[0]: "Cette valeur doit être du texte."
        })
    values = {field: payload.get(field, "") for field in CLIENT_DTO_FIELDS}
    values["country"] = values["country"] or "France"
    return ClientDraft(
        **values,
        proposed_contact_name=current.proposed_contact_name if current else "",
        proposed_contact_role=current.proposed_contact_role if current else "",
    )


def _site_draft(payload: object) -> SiteDraft:
    values = _text_dto(payload, SITE_DTO_FIELDS, "site")
    values["country"] = values["country"] or "France"
    return SiteDraft(**values)


def _site_editor_payload(site: SiteMaster) -> dict[str, str]:
    return {field: getattr(site, field) for field in SITE_DTO_FIELDS}


def _equipment_editor_payload(equipment: EquipmentMaster) -> dict[str, str]:
    return {
        field: "" if getattr(equipment, field) is None else str(getattr(equipment, field))
        for field in EQUIPMENT_DTO_FIELDS
    }


def _equipment_draft(payload: object) -> EquipmentDraft:
    values = _text_dto(payload, EQUIPMENT_DTO_FIELDS, "équipement")
    values["power_kw"] = values["power_kw"] or None
    return EquipmentDraft(**values)


def _text_dto(payload: object, allowed: frozenset[str], label: str) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise MasterDataValidationError({"payload": f"Les informations du {label} sont invalides."})
    unsupported = set(payload) - allowed
    if unsupported:
        raise MasterDataValidationError({
            "payload": f"Certaines informations ne sont pas autorisées pour ce {label}."
        })
    invalid = [field for field, value in payload.items() if not isinstance(value, str)]
    if invalid:
        raise MasterDataValidationError({invalid[0]: "Cette valeur doit être du texte."})
    return {field: payload.get(field, "") for field in allowed}


def _master_error(error: ApplicationError) -> dict:
    fields = getattr(error, "field_errors", {})
    return {"ok": False, "message": error.user_message, "field_errors": fields}


def _closed_contract_dto(payload: object, allowed: frozenset[str]) -> dict:
    if not isinstance(payload, dict):
        raise ContractConditionsValidationError({"payload": "Les conditions transmises sont invalides."})
    unsupported = set(payload) - allowed
    if unsupported:
        raise ContractConditionsValidationError({"payload": "Certaines conditions ne sont pas autorisées."})
    return payload


def _date_fr(value: str | None) -> str:
    if not value:
        return ""
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def _money_fr(value) -> str:
    return format(value, ".2f").replace(".", ",")


class UiBridge(QObject):
    """The deliberately small, presentation-only WebChannel contract."""

    stateChanged = Signal("QVariant")

    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.context = context
        self.navigate_legacy = navigate
        self.filter = ContractRegisterFilter.ALL
        self.search = ""
        self.page_name = "CONTRACTS"
        self.client_search = ""
        self.client_archived = False
        self.selected_client_id: str | None = None
        self.show_archived_master_data = False
        self.contract_id: str | None = None
        self.contract_step = 1
        self.register = ContractRegisterService(
            context.contracts, context.review, context.lifecycle, context.workspace_service,
            RealBackupSummaryProvider(context.backup, context.alerts), context.alerts,
        )

    def snapshot(self) -> dict:
        if self.page_name == "CONTRACT_WORKSPACE":
            return self.contract_workspace_snapshot()
        if self.page_name == "CLIENTS":
            return self.clients_snapshot()
        rows = self.register.rows()
        visible = self.register.filter_rows(rows, self.filter, self.search)
        summary = self.register.backup_summary()
        return {
            "action_count": self.register.action_count(rows),
            "search": self.search,
            "backup": summary.label,
            "filters": [
                {"id": selected.value, "label": label, "active": selected is self.filter}
                for selected, label in (
                    (ContractRegisterFilter.ALL, "Tous"),
                    (ContractRegisterFilter.ACTIONS, "Actions à traiter"),
                    (ContractRegisterFilter.DRAFTS, "Brouillons"),
                    (ContractRegisterFilter.ACTIVE, "Actifs"),
                    (ContractRegisterFilter.TERMINAL, "Terminés"),
                )
            ],
            "rows": [
                {
                    "id": row.contract_id,
                    "number": row.number_label,
                    "updated": "",
                    "client": row.client_name,
                    "site": row.site_label,
                    "status": row.status_label,
                    "status_code": row.status.value,
                    "deadline": row.due_date.strftime("%d/%m/%Y") if row.due_date else "—",
                    "signal": row.signal.label,
                    "needs_action": row.signal.kind is ContractOperationalSignalKind.ACTION,
                    "document": row.document_lines[0] if row.document_lines else "Aucun document",
                }
                for row in visible
            ],
        }

    @staticmethod
    def _address(value) -> str:
        return ", ".join(part for part in (
            value.address_line1, value.address_line2,
            " ".join(part for part in (value.postal_code, value.city) if part), value.country,
        ) if part)

    def contract_workspace_snapshot(self) -> dict:
        contract = self.context.contracts.get(self.contract_id)
        editable = contract.status is ContractStatus.DRAFT
        selected_by_source = {item.source_equipment_id: item for item in contract.equipment_items}
        available_equipment = self.context.contracts.selectable_equipment(contract.id) if editable else []
        available_by_id = {item.id: item for item in available_equipment}
        equipment = []
        for item in contract.equipment_items:
            equipment.append({
                "id": item.source_equipment_id, "item_id": item.id, "selected": True,
                "position": item.position, "name": item.snapshot.display_name or item.snapshot.equipment_type,
                "location": item.snapshot.location, "observation": item.observation,
                "available": item.source_equipment_id in available_by_id,
            })
        for master in available_equipment:
            if master.id not in selected_by_source:
                equipment.append({
                    "id": master.id, "item_id": None, "selected": False, "position": None,
                    "name": master.display_name or master.equipment_type, "location": master.location,
                    "observation": "", "available": True,
                })
        conditions = self.context.contracts.get_conditions(contract.id)
        version = self.context.contracts.selected_template_version(contract.id) if contract.template_version_id else None
        compatible_versions = self.context.contracts.compatible_template_versions(contract.id)
        regime_code = contract.regime.value if contract.regime else None
        selected_compatible = bool(
            version and version.document_kind == "CONTRACT"
            and version.status is TemplateVersionStatus.AVAILABLE
            and version.contract_type_code == contract.type_code.value
            and regime_code in version.allowed_client_regimes
        )
        conclusion_required = bool(
            selected_compatible and version.validation.requires_conclusion(regime_code)
        )
        conclusion_labels = {
            "IN_PREMISES": "Dans les locaux du professionnel",
            "OFF_PREMISES": "Hors établissement",
            "DISTANCE_EMAIL": "À distance par e-mail",
            "ONLINE_INTERFACE": "Interface en ligne",
            "OTHER_DISTANCE": "Autre conclusion à distance",
        }
        authorized_conclusions = tuple(dict.fromkeys(
            item.conclusion_mode for item in version.validation.context_authorizations
            if conclusion_required and item.regime == regime_code and item.conclusion_mode in conclusion_labels
        )) if version else ()
        early_performance_visible = bool(
            conclusion_required and {"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE"}.issubset(
                version.validation.blocks_for(regime_code, conditions.conclusion_mode)
            )
        )
        catalogs = version.catalogs if selected_compatible else None
        resolved_end_date = conditions.resolved_end_date
        vat_amount = conditions.vat_amount
        annual_ttc = conditions.annual_ttc
        regime = {
            "CONSUMER": "Consommateur", "NON_PROFESSIONAL": "Non-professionnel",
            "PROFESSIONAL": "Professionnel",
        }.get(contract.regime.value if contract.regime else None, "Non configuré")
        conclusion = {
            "IN_PREMISES": "Dans les locaux", "OFF_PREMISES": "Hors établissement",
            "DISTANCE_EMAIL": "À distance — e-mail", "ONLINE_INTERFACE": "Interface en ligne",
            "OTHER_DISTANCE": "Autre vente à distance",
        }.get(conditions.conclusion_mode, "Non configuré")
        renewal = {
            "NONE": "Sans renouvellement", "MANUAL": "Renouvellement manuel", "TACIT": "Tacite reconduction",
        }.get(conditions.renewal_mode, "Non configuré")
        complete_step_one = bool(
            contract.client_snapshot and contract.signatory_name.strip() and contract.signatory_role.strip()
            and contract.site_snapshot and contract.equipment_items
        )
        backup = self.register.backup_summary().label
        return {
            "page": "CONTRACT_WORKSPACE", "backup": backup, "active_step": self.contract_step,
            "contract": {
                "id": contract.id, "status": contract.status.value,
                "status_label": STATUS_LABELS[contract.status],
                "number": contract.number or "Brouillon sans numéro", "editable": editable,
                "client_id": contract.client_source_id,
                "client": contract.client_snapshot.display_name if contract.client_snapshot else "",
                "client_address": ({
                    field: getattr(contract.client_snapshot, field) for field in
                    ("address_line1", "address_line2", "postal_code", "city", "country")
                } if contract.client_snapshot else {}),
                "site_id": contract.site_source_id,
                "site": contract.site_snapshot.label if contract.site_snapshot else "",
                "signatory_name": contract.signatory_name, "signatory_role": contract.signatory_role,
                "saved_label": "Enregistré", "document_folder_available": False,
            },
            "clients": [{
                "id": row.client.id, "name": row.client.display_name,
                "type": "Personne" if row.client.party_type == "PERSON" else "Organisation",
                "secondary": row.client.email or row.client.phone or self._address(row.client),
            } for row in self.context.contracts.selectable_clients()],
            "sites": [{
                "id": site.id, "name": site.label, "address": self._address(site),
            } for site in (self.context.contracts.selectable_sites(contract.id) if editable else [])],
            "equipment": equipment,
            "conditions_b1": {
                "regime": regime_code,
                "regime_options": [
                    {"id": "CONSUMER", "label": "Consommateur"},
                    {"id": "NON_PROFESSIONAL", "label": "Non-professionnel"},
                    {"id": "PROFESSIONAL", "label": "Professionnel"},
                ],
                "templates": [{
                    "id": item.id, "template_id": item.template_id,
                    "name": item.template_name, "version": item.version,
                    "display": item.display_name,
                } for item in compatible_versions],
                "selected_template_id": contract.template_id,
                "selected_template_version_id": contract.template_version_id,
                "selected_template_display": version.display_name if version else "",
                "selected_template_compatible": selected_compatible,
                "model_state": (
                    "REGIME_REQUIRED" if regime_code is None else
                    "NO_MODEL" if not compatible_versions else "AVAILABLE"
                ),
                "conclusion_required": conclusion_required,
                "conclusion_mode": conditions.conclusion_mode,
                "conclusion_options": [
                    {"id": code, "label": conclusion_labels[code]} for code in authorized_conclusions
                ],
                "early_performance_visible": early_performance_visible,
                "early_performance_requested": conditions.early_performance_requested,
                "visits_per_year": conditions.visits_per_year,
                "refrigerant_handling_mode": conditions.refrigerant_handling_mode,
                "included_options": list(conditions.included_options),
                "priority_breakdown": conditions.priority_breakdown,
                "priority_breakdown_delay": conditions.priority_breakdown_delay or "",
            },
            "conditions_b2": {
                "issue_date": conditions.issue_date,
                "start_date": conditions.start_date,
                "initial_duration_mode": conditions.initial_duration_mode,
                "initial_duration_months": conditions.initial_duration_months,
                "initial_end_date": conditions.initial_end_date,
                "resolved_end_date": resolved_end_date,
                "signature_city": conditions.signature_city,
                "included_area": conditions.included_area,
                "business_hours": conditions.business_hours,
                "travel_included": conditions.travel_included,
                "missed_appointment_fee": (
                    _money_fr(Decimal(conditions.missed_appointment_fee))
                    if conditions.missed_appointment_fee is not None else None
                ),
                "additional_exclusions": conditions.additional_exclusions,
                "annual_ht": (
                    _money_fr(Decimal(conditions.annual_ht))
                    if conditions.annual_ht is not None else None
                ),
                "vat_rate": conditions.vat_rate,
                "vat_amount": _money_fr(vat_amount) if vat_amount is not None else "",
                "annual_ttc": _money_fr(annual_ttc) if annual_ttc is not None else "",
                "vat_rates": list(catalogs.vat_rates) if catalogs else [],
                "payment_terms_code": conditions.payment_terms_code,
                "payment_due_days": conditions.payment_due_days,
                "payment_terms_custom_text": conditions.payment_terms_custom_text,
                "payment_terms": [{
                    "id": item.code, "label": item.label,
                    "requires_day_count": item.requires_day_count,
                    "allows_custom_text": item.allows_custom_text,
                } for item in (catalogs.payment_terms if catalogs else ())],
                "payment_methods": list(conditions.payment_methods),
                "payment_method_options": [
                    {"id": item.code, "label": item.label}
                    for item in (catalogs.payment_methods if catalogs else ())
                ],
                "pricing_catalog_available": catalogs is not None,
            },
            "summary": {
                "client": contract.client_snapshot.display_name if contract.client_snapshot else "Non sélectionné",
                "signatory": " · ".join(part for part in (contract.signatory_name, contract.signatory_role) if part) or "Non renseigné",
                "site": contract.site_snapshot.label if contract.site_snapshot else "Non sélectionné",
                "equipment": [item.snapshot.display_name or item.snapshot.equipment_type for item in contract.equipment_items],
                "regime": regime, "conclusion": conclusion,
                "period": (
                    f"Du {_date_fr(conditions.start_date)} au {_date_fr(resolved_end_date)}"
                    if conditions.start_date and resolved_end_date else "Non configurée"
                ),
                "price": (
                    f"{_money_fr(annual_ttc)} € TTC / an" if annual_ttc is not None else "Non configuré"
                ),
                "renewal": renewal,
                "template": version.display_name if version else "Non configuré",
                "completion": (
                    "Étape 2 à compléter" if self.contract_step == 2
                    else "Étape 1 complète" if complete_step_one else "Étape 1 à compléter"
                ),
            },
        }

    def clients_snapshot(self) -> dict:
        clients = self.context.master_data.list_clients(self.client_search, self.client_archived)
        if self.selected_client_id is None or not any(item.client.id == self.selected_client_id for item in clients):
            self.selected_client_id = clients[0].client.id if clients else None
        selected = next((item.client for item in clients if item.client.id == self.selected_client_id), None)
        sites = [] if selected is None else self.context.master_data.list_sites(selected.id)
        all_contracts = [
            self.context.contracts.get(item.id)
            for item in self.context.contracts.list_drafts()
        ]
        linked = [] if selected is None else [
            contract for contract in all_contracts if contract.client_source_id == selected.id
        ]
        referenced_site_ids = {
            contract.site_source_id for contract in all_contracts if contract.site_source_id
        }
        referenced_equipment_ids = {
            item.source_equipment_id
            for contract in all_contracts
            for item in contract.equipment_items
        }
        archived_children = any(site.archived for site in sites) or any(
            equipment.archived
            for site in sites
            for equipment in self.context.master_data.list_equipment(site.id)
        )
        visible_sites = [
            site for site in sites if self.show_archived_master_data or not site.archived
        ]
        site_rows = []
        for site in visible_sites:
            equipment = [
                item for item in self.context.master_data.list_equipment(site.id)
                if self.show_archived_master_data or not item.archived
            ]
            site_rows.append({
                "id": site.id,
                "label": site.label,
                "address": site.rendered_address,
                "archived": site.archived,
                "referenced": site.id in referenced_site_ids,
                "active_equipment_count": sum(not item.archived for item in self.context.master_data.list_equipment(site.id)),
                "editor": _site_editor_payload(site),
                "equipment": [{
                    "id": item.id,
                    "name": item.display_name,
                    "location": item.location,
                    "archived": item.archived,
                    "referenced": item.id in referenced_equipment_ids,
                    "editor": _equipment_editor_payload(item),
                } for item in equipment],
            })
        return {
            "page": "CLIENTS",
            "search": self.client_search,
            "archived": self.client_archived,
            "show_archived_master_data": self.show_archived_master_data,
            "backup": self.register.backup_summary().label,
            "clients": [{
                "id": item.client.id,
                "name": item.client.display_name,
                "type": "Personne" if item.client.party_type == "PERSON" else "Organisation",
                "summary": f"{item.site_count} site(s) · {item.equipment_count} équipement(s)",
                "archived": item.client.archived,
                "selected": item.client.id == self.selected_client_id,
            } for item in clients],
            "selected": None if selected is None else {
                "id": selected.id,
                "name": selected.display_name,
                "type": "Personne" if selected.party_type == "PERSON" else "Organisation",
                "address": selected.rendered_address,
                "email": selected.email,
                "phone": selected.phone,
                "archived": selected.archived,
                "referenced": bool(linked),
                "has_archived_children": archived_children,
                "legal_form": selected.legal_form,
                "siret": selected.siret,
                "billing_address": selected.billing_address,
                "internal_reference": selected.internal_reference,
                "editor": _client_editor_payload(selected),
                "sites": site_rows,
                "linked": [{
                    "id": contract.id,
                    "number": contract.number or "Brouillon sans numéro",
                    "site": contract.site_snapshot.label if contract.site_snapshot else "Site à sélectionner",
                    "status_label": STATUS_LABELS[contract.status],
                    "status_tone": contract.status.value,
                    "secondary_display": f"Entretien annuel · {contract.site_snapshot.label if contract.site_snapshot else 'Site à sélectionner'}",
                } for contract in linked],
            },
        }

    @Slot()
    def refresh(self) -> None:
        self.stateChanged.emit(self.snapshot())

    @Slot(str)
    def setFilter(self, value: str) -> None:
        self.filter = ContractRegisterFilter(value)
        self.refresh()

    @Slot(str)
    def setSearch(self, value: str) -> None:
        self.search = value
        self.refresh()

    @Slot(str, result="QVariant")
    def openContract(self, contract_id: str) -> dict:
        try:
            self.context.contracts.get(contract_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.page_name = "CONTRACT_WORKSPACE"
        self.contract_step = 1
        self.refresh()
        return {"ok": True, "id": contract_id}

    @Slot(result="QVariant")
    def createContract(self) -> dict:
        try:
            contract = self.context.contracts.create_draft()
        except ApplicationError as error:
            return _master_error(error)
        self.openContract(contract.id)
        return {"ok": True, "id": contract.id}

    @Slot()
    def saveBackup(self) -> None:
        self.context.backup.create_now()
        self.refresh()

    @Slot(str)
    def navigate(self, destination: str) -> None:
        if destination == "CLIENTS":
            self.page_name = "CLIENTS"
            self.refresh()
            return
        if destination == "CONTRACTS":
            self.page_name = "CONTRACTS"
            self.contract_id = None
            self.refresh()
            return
        if destination in {"CLIENTS", "SETTINGS"}:
            self.navigate_legacy(destination)

    @Slot(str)
    def setClientSearch(self, value: str) -> None:
        self.client_search = value
        self.refresh()

    @Slot(bool)
    def setClientArchived(self, archived: bool) -> None:
        self.client_archived = archived
        self.show_archived_master_data = False
        self.refresh()

    @Slot(bool)
    def setShowArchivedMasterData(self, visible: bool) -> None:
        self.show_archived_master_data = visible
        self.refresh()

    @Slot(str)
    def selectClient(self, client_id: str) -> None:
        self.selected_client_id = client_id
        self.refresh()

    @Slot()
    def returnToContracts(self) -> None:
        self.page_name = "CONTRACTS"
        self.contract_id = None
        self.contract_step = 1
        self.refresh()

    @Slot(str, int, result="QVariant")
    def setContractStep(self, contract_id: str, step: int) -> dict:
        if contract_id != self.contract_id or step not in {1, 2}:
            return {"ok": False, "message": "Cette étape n’est pas disponible."}
        self.contract_step = step
        self.refresh()
        return {"ok": True, "id": contract_id, "step": step}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractFramework(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_FRAMEWORK_DTO_FIELDS)
            if not values:
                raise ContractConditionsValidationError({"payload": "Aucune condition à enregistrer."})
            contract = self.context.contracts.get(contract_id)
            if "regime" in values:
                regime = values["regime"]
                if regime is not None and not isinstance(regime, str):
                    raise ContractConditionsValidationError({"regime": "Le régime est invalide."})
                contract = self.context.contracts.change_regime(contract_id, regime)
                compatible = self.context.contracts.compatible_template_versions(contract_id)
                if contract.template_version_id is None and len(compatible) == 1:
                    contract = self.context.contracts.select_template_version(contract_id, compatible[0].id)
            context_fields = {"conclusion_mode", "early_performance_requested"} & set(values)
            if context_fields:
                current = self.context.contracts.get_conditions(contract_id)
                updated = asdict(current)
                if "conclusion_mode" in values:
                    conclusion = values["conclusion_mode"]
                    if conclusion is not None and not isinstance(conclusion, str):
                        raise ContractConditionsValidationError({"conclusion_mode": "Le mode de conclusion est invalide."})
                    updated["conclusion_mode"] = conclusion
                if "early_performance_requested" in values:
                    early = values["early_performance_requested"]
                    if early is not None and not isinstance(early, bool):
                        raise ContractConditionsValidationError({"early_performance_requested": "Cette réponse est invalide."})
                    updated["early_performance_requested"] = early
                self.context.contracts.save_conditions(contract_id, ContractConditions(**updated))
                contract = self.context.contracts.get(contract_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {
            "ok": True, "id": contract.id,
            "regime": contract.regime.value if contract.regime else None,
            "template_version_id": contract.template_version_id,
        }

    @Slot(str, str, result="QVariant")
    def selectContractTemplateVersion(self, contract_id: str, version_id: str) -> dict:
        try:
            contract = self.context.contracts.select_template_version(contract_id, version_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "template_version_id": contract.template_version_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractServiceOffer(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_SERVICE_DTO_FIELDS)
            missing = CONTRACT_SERVICE_DTO_FIELDS - set(values)
            if missing:
                raise ContractConditionsValidationError({"payload": "Complétez l’offre de services avant de l’enregistrer."})
            visits = values["visits_per_year"]
            if isinstance(visits, bool) or not isinstance(visits, (int, float)) or int(visits) != visits or visits < 1:
                raise ContractConditionsValidationError({"visits_per_year": "Le nombre de visites doit être au moins égal à 1."})
            refrigerant = values["refrigerant_handling_mode"]
            if refrigerant not in {"IN_HOUSE_AUTHORIZED", "PARTNER", "EXCLUDED"}:
                raise ContractConditionsValidationError({"refrigerant_handling_mode": "Choisissez la gestion des fluides."})
            options = values["included_options"]
            if not isinstance(options, list) or any(
                option not in {"DEEP_CLEANING", "DISINFECTION"} for option in options
            ):
                raise ContractConditionsValidationError({"included_options": "Une prestation incluse est inconnue."})
            priority = values["priority_breakdown"]
            if not isinstance(priority, bool):
                raise ContractConditionsValidationError({"priority_breakdown": "Indiquez si le dépannage prioritaire est inclus."})
            delay = values["priority_breakdown_delay"]
            if priority and (not isinstance(delay, str) or not delay.strip()):
                raise ContractConditionsValidationError({"priority_breakdown_delay": "Renseignez le délai d’intervention."})
            if not priority:
                delay = None
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(
                visits_per_year=int(visits), refrigerant_handling_mode=refrigerant,
                included_options=tuple(dict.fromkeys(options)), priority_breakdown=priority,
                priority_breakdown_delay=delay,
            )
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "visits_per_year": saved.visits_per_year}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractPeriod(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_PERIOD_DTO_FIELDS)
            if set(values) != CONTRACT_PERIOD_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les informations de période transmises."})
            for field in ("issue_date", "start_date", "initial_duration_mode", "initial_end_date"):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur est invalide."})
            if not isinstance(values["signature_city"], str):
                raise ContractConditionsValidationError({"signature_city": "La ville de signature est invalide."})
            months = values["initial_duration_months"]
            if months is not None and (isinstance(months, bool) or not isinstance(months, (int, float)) or int(months) != months):
                raise ContractConditionsValidationError({"initial_duration_months": "Saisissez un nombre entier valide."})
            if (
                values["initial_duration_mode"] == "CUSTOM"
                and values["start_date"] and values["initial_end_date"]
            ):
                try:
                    incoherent = date.fromisoformat(values["initial_end_date"]) < date.fromisoformat(values["start_date"])
                except ValueError:
                    incoherent = False  # The authoritative service reports malformed dates below.
                if incoherent:
                    raise ContractConditionsValidationError({
                        "initial_end_date": "La date de fin doit être postérieure à la date de prise d’effet."
                    })
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            current["initial_duration_months"] = int(months) if months is not None else None
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "resolved_end_date": saved.resolved_end_date}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractInterventionConditions(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_INTERVENTION_DTO_FIELDS)
            if set(values) != CONTRACT_INTERVENTION_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les conditions d’intervention transmises."})
            for field in ("included_area", "business_hours", "additional_exclusions"):
                if not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur doit être du texte."})
            if values["travel_included"] is not None and not isinstance(values["travel_included"], bool):
                raise ContractConditionsValidationError({"travel_included": "Choisissez les conditions de déplacement."})
            fee = values["missed_appointment_fee"]
            if fee is not None and not isinstance(fee, str):
                raise ContractConditionsValidationError({"missed_appointment_fee": "Le montant est invalide."})
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {"ok": True, "id": contract_id, "travel_included": saved.travel_included}

    @Slot(str, "QVariant", result="QVariant")
    def updateContractPricing(self, contract_id: str, payload: object) -> dict:
        try:
            values = _closed_contract_dto(payload, CONTRACT_PRICING_DTO_FIELDS)
            if set(values) != CONTRACT_PRICING_DTO_FIELDS:
                raise ContractConditionsValidationError({"payload": "Complétez les informations de prix transmises."})
            for field in ("annual_ht", "vat_rate", "payment_terms_code"):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ContractConditionsValidationError({field: "Cette valeur est invalide."})
            if not isinstance(values["payment_terms_custom_text"], str):
                raise ContractConditionsValidationError({"payment_terms_custom_text": "Cette précision doit être du texte."})
            due_days = values["payment_due_days"]
            if due_days is not None and (
                isinstance(due_days, bool) or not isinstance(due_days, (int, float)) or int(due_days) != due_days
            ):
                raise ContractConditionsValidationError({"payment_due_days": "Saisissez un nombre entier valide."})
            methods = values["payment_methods"]
            if not isinstance(methods, list) or any(not isinstance(item, str) for item in methods):
                raise ContractConditionsValidationError({"payment_methods": "Les moyens de paiement sont invalides."})
            current = asdict(self.context.contracts.get_conditions(contract_id))
            current.update(values)
            current["payment_due_days"] = int(due_days) if due_days is not None else None
            current["payment_methods"] = tuple(methods)
            saved = self.context.contracts.save_conditions(contract_id, ContractConditions(**current))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract_id
        self.refresh()
        return {
            "ok": True, "id": contract_id,
            "vat_amount": _money_fr(saved.vat_amount) if saved.vat_amount is not None else "",
            "annual_ttc": _money_fr(saved.annual_ttc) if saved.annual_ttc is not None else "",
        }

    @Slot()
    def openContractModels(self) -> None:
        self.navigate_legacy("SETTINGS_MODELS")

    @Slot(str, str, result="QVariant")
    def selectContractClient(self, contract_id: str, client_id: str) -> dict:
        try:
            contract = self.context.contracts.select_client(contract_id, client_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "client_id": contract.client_source_id}

    @Slot(str, str, str, result="QVariant")
    def updateContractSignatory(self, contract_id: str, name: str, role: str) -> dict:
        try:
            contract = self.context.contracts.update_signatory(contract_id, name, role)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id}

    @Slot(str, str, result="QVariant")
    def selectContractSite(self, contract_id: str, site_id: str) -> dict:
        try:
            contract = self.context.contracts.select_site(contract_id, site_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": contract.site_source_id}

    @Slot(str, str, bool, result="QVariant")
    def setContractEquipment(self, contract_id: str, equipment_id: str, selected: bool) -> dict:
        try:
            operation = self.context.contracts.select_equipment if selected else self.context.contracts.deselect_equipment
            contract = operation(contract_id, equipment_id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "equipment_id": equipment_id, "selected": selected}

    @Slot(str, str, int, result="QVariant")
    def moveContractEquipment(self, contract_id: str, item_id: str, delta: int) -> dict:
        try:
            contract = self.context.contracts.move_equipment(contract_id, item_id, delta)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "item_id": item_id}

    @Slot(str, str, str, result="QVariant")
    def updateContractEquipmentObservation(self, contract_id: str, item_id: str, observation: str) -> dict:
        try:
            contract = self.context.contracts.update_observation(contract_id, item_id, observation)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "item_id": item_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractClient(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_client(contract_id, _client_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "client_id": contract.client_source_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractSite(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_site(contract_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": contract.site_source_id}

    @Slot(str, "QVariant", result="QVariant")
    def createContractEquipment(self, contract_id: str, payload: object) -> dict:
        try:
            contract = self.context.contracts.create_and_select_equipment(contract_id, _equipment_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        created = contract.equipment_items[-1]
        self.contract_id = contract.id
        self.refresh()
        return {"ok": True, "id": contract.id, "equipment_id": created.source_equipment_id, "item_id": created.id}

    @Slot("QVariant", result="QVariant")
    def createClient(self, payload: object) -> dict:
        try:
            created = self.context.master_data.create_client(_client_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = created.id
        self.refresh()
        return {"ok": True, "id": created.id}

    @Slot(str, "QVariant", result="QVariant")
    def updateClient(self, client_id: str, payload: object) -> dict:
        try:
            current = self.context.master_data.get_client(client_id)
            updated = self.context.master_data.update_client(
                client_id, _client_draft(payload, current)
            )
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = updated.id
        self.refresh()
        return {"ok": True, "id": updated.id}

    @Slot(str, "QVariant", result="QVariant")
    def createSite(self, client_id: str, payload: object) -> dict:
        try:
            created = self.context.master_data.create_site(client_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = created.client_id
        self.refresh()
        return {"ok": True, "id": created.id, "client_id": created.client_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateSite(self, site_id: str, payload: object) -> dict:
        try:
            updated = self.context.master_data.update_site(site_id, _site_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = updated.client_id
        self.refresh()
        return {"ok": True, "id": updated.id, "client_id": updated.client_id}

    @Slot(str, "QVariant", result="QVariant")
    def createEquipment(self, site_id: str, payload: object) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            created = self.context.master_data.create_equipment(site_id, _equipment_draft(payload))
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": created.id, "site_id": created.site_id}

    @Slot(str, "QVariant", result="QVariant")
    def updateEquipment(self, equipment_id: str, payload: object) -> dict:
        try:
            current = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(current.site_id)
            updated = self.context.master_data.update_equipment(
                equipment_id, _equipment_draft(payload)
            )
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": updated.id, "site_id": updated.site_id}

    @Slot(str, result="QVariant")
    def archiveClient(self, client_id: str) -> dict:
        try:
            client = self.context.master_data.get_client(client_id)
            self.context.master_data.archive_client(client.id)
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = True
        self.selected_client_id = client.id
        self.show_archived_master_data = False
        self.refresh()
        return {"ok": True, "id": client.id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreClient(self, client_id: str) -> dict:
        try:
            client = self.context.master_data.get_client(client_id)
            self.context.master_data.restore_client(client.id)
        except ApplicationError as error:
            return _master_error(error)
        self.client_archived = False
        self.selected_client_id = client.id
        self.show_archived_master_data = False
        self.refresh()
        return {"ok": True, "id": client.id, "archived": False}

    @Slot(str, result="QVariant")
    def archiveSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            self.context.master_data.archive_site(site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": site.id, "client_id": site.client_id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            self.context.master_data.restore_site(site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": site.id, "client_id": site.client_id, "archived": False}

    @Slot(str, result="QVariant")
    def archiveEquipment(self, equipment_id: str) -> dict:
        try:
            equipment = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(equipment.site_id)
            self.context.master_data.archive_equipment(equipment.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": equipment.id, "site_id": equipment.site_id, "archived": True}

    @Slot(str, result="QVariant")
    def restoreEquipment(self, equipment_id: str) -> dict:
        try:
            equipment = self.context.master_data.get_equipment(equipment_id)
            site = self.context.master_data.get_site(equipment.site_id)
            self.context.master_data.restore_equipment(equipment.id)
        except ApplicationError as error:
            return _master_error(error)
        self.selected_client_id = site.client_id
        self.refresh()
        return {"ok": True, "id": equipment.id, "site_id": equipment.site_id, "archived": False}

    @Slot(str)
    def openLinkedContract(self, contract_id: str) -> None:
        self.openContract(contract_id)

    @Slot(str)
    def editSite(self, site_id: str) -> None:
        self.navigate_legacy(f"EDIT_SITE:{site_id}")

    @Slot(str)
    def editEquipment(self, equipment_id: str) -> None:
        self.navigate_legacy(f"EDIT_EQUIPMENT:{equipment_id}")

    @Slot(str)
    def addEquipment(self, site_id: str) -> None:
        self.navigate_legacy(f"ADD_EQUIPMENT:{site_id}")

    @Slot(str, result="QVariant")
    def createContractForSite(self, site_id: str) -> dict:
        try:
            site = self.context.master_data.get_site(site_id)
            client = self.context.master_data.get_client(site.client_id)
            if site.archived or client.archived:
                raise MasterDataValidationError({
                    "site_id": "Un contrat ne peut pas être créé pour un site ou un client archivé."
                })
            contract = self.context.contracts.create_draft()
            self.context.contracts.select_client(contract.id, client.id)
            contract = self.context.contracts.select_site(contract.id, site.id)
        except ApplicationError as error:
            return _master_error(error)
        self.contract_id = contract.id
        self.page_name = "CONTRACT_WORKSPACE"
        self.refresh()
        return {"ok": True, "id": contract.id, "site_id": site.id, "client_id": client.id}


class TrustedLocalPage(QWebEnginePage):
    """Allow only this packaged local web surface; popups and remote navigation are denied."""

    def __init__(self, assets_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.assets_root = assets_root.resolve()

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:
        if not url.isLocalFile():
            return False
        try:
            url.toLocalFile() and Path(url.toLocalFile()).resolve().relative_to(self.assets_root)
        except ValueError:
            return False
        return True

    def createWindow(self, window_type):  # noqa: N802 - Qt API spelling
        return None


class WebUiHost(QWebEngineView):
    def __init__(self, context, navigate: Callable[[str], None]) -> None:
        super().__init__()
        self.assets_root = Path(__file__).parent.parent / "ui_web"
        self._showing_load_error = False
        self.bridge = UiBridge(context, navigate)
        page = TrustedLocalPage(self.assets_root, self)
        self.setPage(page)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._load_finished)
        self.load(QUrl.fromLocalFile(str(self.assets_root / "index.html")))

    def _load_finished(self, success: bool) -> None:
        if success or self._showing_load_error:
            return
        self._showing_load_error = True
        self.setHtml(
            "<main style='font-family:Segoe UI,Arial;padding:32px'>"
            "<h1>Contrats indisponibles</h1>"
            "<p>La surface locale Contrats ne peut pas être chargée. Redémarrez l’application.</p>"
            "</main>"
        )
