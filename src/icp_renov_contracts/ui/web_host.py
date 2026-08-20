from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..domain import (
    ClientDraft, ClientMaster, ContractStatus, EquipmentDraft, EquipmentMaster, SiteDraft, SiteMaster,
)
from ..errors import ApplicationError, MasterDataValidationError
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
    fields = error.field_errors if isinstance(error, MasterDataValidationError) else {}
    return {"ok": False, "message": error.user_message, "field_errors": fields}


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
        regime = {
            "CONSUMER": "Consommateur", "NON_PROFESSIONAL": "Non-professionnel",
            "PROFESSIONAL": "Professionnel",
        }.get(contract.regime.value if contract.regime else None, "Non configuré")
        conclusion = {
            "IN_PREMISES": "Dans les locaux", "OFF_PREMISES": "Hors établissement",
            "DISTANCE_EMAIL": "À distance — e-mail", "ONLINE_INTERFACE": "Interface en ligne",
            "OTHER_DISTANCE": "Autre vente à distance",
        }.get(conditions.conclusion_mode, "Non configuré")
        period = "Non configurée"
        if conditions.start_date:
            period = conditions.start_date
            if conditions.resolved_end_date:
                period += f" → {conditions.resolved_end_date}"
        renewal = {
            "NONE": "Sans renouvellement", "MANUAL": "Renouvellement manuel", "TACIT": "Tacite reconduction",
        }.get(conditions.renewal_mode, "Non configuré")
        complete_step_one = bool(
            contract.client_snapshot and contract.signatory_name.strip() and contract.signatory_role.strip()
            and contract.site_snapshot and contract.equipment_items
        )
        backup = self.register.backup_summary().label
        return {
            "page": "CONTRACT_WORKSPACE", "backup": backup,
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
            "summary": {
                "client": contract.client_snapshot.display_name if contract.client_snapshot else "Non sélectionné",
                "signatory": " · ".join(part for part in (contract.signatory_name, contract.signatory_role) if part) or "Non renseigné",
                "site": contract.site_snapshot.label if contract.site_snapshot else "Non sélectionné",
                "equipment": [item.snapshot.display_name or item.snapshot.equipment_type for item in contract.equipment_items],
                "regime": regime, "conclusion": conclusion, "period": period,
                "price": f"{conditions.annual_ht} € HT / an" if conditions.annual_ht else "Non configuré",
                "renewal": renewal,
                "template": version.display_name if version else "Non configuré",
                "completion": "Étape 1 complète" if complete_step_one else "Étape 1 à compléter",
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
        self.refresh()

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
