from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import uuid

from ..domain import (
    ClientDraft, ClientSnapshot, Contract, ContractEquipmentItem, EquipmentDraft,
    EquipmentSnapshot, SiteDraft, SiteSnapshot,
)
from ..errors import ContractNotFoundError, ContractPersistenceError, ContractValidationError
from ..repositories import ContractRepository
from .master_data import MasterDataService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContractService:
    def __init__(self, repository: ContractRepository, master_data: MasterDataService) -> None:
        self.repository = repository
        self.master_data = master_data

    def create_draft(self) -> Contract:
        contract_id = str(uuid.uuid4())
        self._persist(self.repository.create, contract_id, _now())
        return self.get(contract_id)

    def get(self, contract_id: str) -> Contract:
        contract = self.repository.get(contract_id)
        if contract is None: raise ContractNotFoundError(contract_id)
        return contract

    def list_drafts(self):
        return self.repository.list_drafts()

    def selectable_clients(self, search: str = ""):
        return self.master_data.list_clients(search=search, archived=False)

    def select_client(self, contract_id: str, client_id: str) -> Contract:
        current = self.get(contract_id)
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
        return self.select_client(contract_id, self.master_data.create_client(draft).id)

    def selectable_sites(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.client_source_id is None: return []
        return [site for site in self.master_data.list_sites(contract.client_source_id) if not site.archived]

    def select_site(self, contract_id: str, site_id: str) -> Contract:
        current = self.get(contract_id)
        if current.client_source_id is None: raise ContractValidationError("client required")
        site = self.master_data.get_site(site_id)
        if site.archived or site.client_id != current.client_source_id:
            raise ContractValidationError("site outside selected client")
        if current.site_source_id == site_id:
            return current
        self._persist(self.repository.select_site, contract_id, site.id, SiteSnapshot.from_master(site), _now())
        return self.get(contract_id)

    def create_and_select_site(self, contract_id: str, draft: SiteDraft) -> Contract:
        contract = self.get(contract_id)
        if contract.client_source_id is None: raise ContractValidationError("client required")
        return self.select_site(contract_id, self.master_data.create_site(contract.client_source_id, draft).id)

    def selectable_equipment(self, contract_id: str):
        contract = self.get(contract_id)
        if contract.site_source_id is None: return []
        return [item for item in self.master_data.list_equipment(contract.site_source_id) if not item.archived]

    def select_equipment(self, contract_id: str, equipment_id: str) -> Contract:
        contract = self.get(contract_id)
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
        contract = self.get(contract_id)
        if contract.site_source_id is None: raise ContractValidationError("site required")
        equipment = self.master_data.create_equipment(contract.site_source_id, draft)
        return self.select_equipment(contract_id, equipment.id)

    def deselect_equipment(self, contract_id: str, equipment_id: str) -> Contract:
        contract = self.get(contract_id)
        item = next((value for value in contract.equipment_items if value.source_equipment_id == equipment_id), None)
        if item is None: raise ContractValidationError("equipment not selected")
        remaining = [value.id for value in contract.equipment_items if value.id != item.id]
        self._persist(self.repository.remove_equipment, contract_id, item.id, remaining, _now())
        return self.get(contract_id)

    def move_equipment(self, contract_id: str, item_id: str, delta: int) -> Contract:
        if delta not in {-1, 1}: raise ContractValidationError("invalid movement")
        contract = self.get(contract_id)
        ids = [item.id for item in contract.equipment_items]
        if item_id not in ids: raise ContractValidationError("item not selected")
        index = ids.index(item_id)
        target = index + delta
        if target < 0 or target >= len(ids): return contract
        ids[index], ids[target] = ids[target], ids[index]
        self._persist(self.repository.reorder, contract_id, ids, _now())
        return self.get(contract_id)

    def update_observation(self, contract_id: str, item_id: str, observation: str) -> Contract:
        contract = self.get(contract_id)
        if item_id not in {item.id for item in contract.equipment_items}:
            raise ContractValidationError("item not selected")
        self._persist(self.repository.update_observation, contract_id, item_id, observation.strip(), _now())
        return self.get(contract_id)

    def update_signatory(self, contract_id: str, name: str, role: str) -> Contract:
        self.get(contract_id)
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
