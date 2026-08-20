from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from ..domain import ClientDraft, ClientMaster, EquipmentDraft, EquipmentMaster, SiteDraft, SiteMaster
from ..errors import MasterDataNotFoundError, MasterDataPersistenceError, MasterDataValidationError
from ..repositories import MasterDataRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str) -> str:
    return value.strip()


class MasterDataService:
    def __init__(self, repository: MasterDataRepository) -> None:
        self.repository = repository

    def list_clients(self, search: str = "", archived: bool = False):
        return self.repository.list_clients(search, archived)

    def get_client(self, client_id: str) -> ClientMaster:
        client = self.repository.get_client(client_id)
        if client is None:
            raise MasterDataNotFoundError(client_id)
        return client

    def create_client(self, draft: ClientDraft) -> ClientMaster:
        clean = self._validate_client(draft)
        now = _now()
        client = ClientMaster(**clean.__dict__, id=str(uuid.uuid4()), created_at_utc=now, updated_at_utc=now)
        self._persist(self.repository.create_client, client)
        return client

    def update_client(self, client_id: str, draft: ClientDraft) -> ClientMaster:
        current = self.get_client(client_id)
        clean = self._validate_client(draft)
        if clean.party_type != current.party_type:
            raise MasterDataValidationError({"party_type": "Le type d’une fiche existante ne peut pas être modifié."})
        updated = ClientMaster(
            **clean.__dict__, id=current.id, archived=current.archived,
            created_at_utc=current.created_at_utc, updated_at_utc=_now(),
        )
        self._persist(self.repository.update_client, updated)
        return updated

    def archive_client(self, client_id: str) -> None:
        self.get_client(client_id)
        self._persist(self.repository.set_client_archived, client_id, True, _now())

    def restore_client(self, client_id: str) -> None:
        self.get_client(client_id)
        self._persist(self.repository.set_client_archived, client_id, False, _now())

    def list_sites(self, client_id: str) -> list[SiteMaster]:
        self.get_client(client_id)
        return self.repository.list_sites(client_id)

    def get_site(self, site_id: str) -> SiteMaster:
        site = self.repository.get_site(site_id)
        if site is None:
            raise MasterDataNotFoundError(site_id)
        return site

    def create_site(self, client_id: str, draft: SiteDraft) -> SiteMaster:
        client = self.get_client(client_id)
        if client.archived:
            raise MasterDataValidationError({
                "client_id": "Un site ne peut pas être ajouté à un client archivé."
            })
        clean = self._validate_site(draft)
        now = _now()
        site = SiteMaster(**clean.__dict__, id=str(uuid.uuid4()), client_id=client_id, created_at_utc=now, updated_at_utc=now)
        self._persist(self.repository.create_site, site)
        return site

    def update_site(self, site_id: str, draft: SiteDraft) -> SiteMaster:
        current = self.get_site(site_id)
        clean = self._validate_site(draft)
        updated = SiteMaster(
            **clean.__dict__, id=current.id, client_id=current.client_id, archived=current.archived,
            created_at_utc=current.created_at_utc, updated_at_utc=_now(),
        )
        self._persist(self.repository.update_site, updated)
        return updated

    def archive_site(self, site_id: str) -> None:
        self.get_site(site_id)
        self._persist(self.repository.set_site_archived, site_id, True, _now())

    def restore_site(self, site_id: str) -> None:
        self.get_site(site_id)
        self._persist(self.repository.set_site_archived, site_id, False, _now())

    def list_equipment(self, site_id: str) -> list[EquipmentMaster]:
        self.get_site(site_id)
        return self.repository.list_equipment(site_id)

    def get_equipment(self, equipment_id: str) -> EquipmentMaster:
        equipment = self.repository.get_equipment(equipment_id)
        if equipment is None:
            raise MasterDataNotFoundError(equipment_id)
        return equipment

    def create_equipment(self, site_id: str, draft: EquipmentDraft) -> EquipmentMaster:
        site = self.get_site(site_id)
        client = self.get_client(site.client_id)
        if site.archived or client.archived:
            raise MasterDataValidationError({
                "site_id": "Un équipement ne peut pas être ajouté dans un site ou un client archivé."
            })
        clean = self._validate_equipment(draft)
        now = _now()
        equipment = EquipmentMaster(
            **clean.__dict__, id=str(uuid.uuid4()), site_id=site_id,
            created_at_utc=now, updated_at_utc=now,
        )
        self._persist(self.repository.create_equipment, equipment)
        return equipment

    def update_equipment(self, equipment_id: str, draft: EquipmentDraft) -> EquipmentMaster:
        current = self.get_equipment(equipment_id)
        clean = self._validate_equipment(draft)
        updated = EquipmentMaster(
            **clean.__dict__, id=current.id, site_id=current.site_id, archived=current.archived,
            created_at_utc=current.created_at_utc, updated_at_utc=_now(),
        )
        self._persist(self.repository.update_equipment, updated)
        return updated

    def archive_equipment(self, equipment_id: str) -> None:
        self.get_equipment(equipment_id)
        self._persist(self.repository.set_equipment_archived, equipment_id, True, _now())

    def restore_equipment(self, equipment_id: str) -> None:
        self.get_equipment(equipment_id)
        self._persist(self.repository.set_equipment_archived, equipment_id, False, _now())

    @staticmethod
    def _validate_client(draft: ClientDraft) -> ClientDraft:
        values = {key: _clean(value) if isinstance(value, str) else value for key, value in draft.__dict__.items()}
        errors: dict[str, str] = {}
        if values["party_type"] not in {"PERSON", "ORGANIZATION"}:
            errors["party_type"] = "Choisissez Personne ou Organisation."
        if values["party_type"] == "PERSON":
            if not values["first_name"]: errors["first_name"] = "Le prénom est requis."
            if not values["last_name"]: errors["last_name"] = "Le nom est requis."
            values["organization_name"] = values["legal_form"] = values["siret"] = ""
            values["billing_address"] = values["proposed_contact_name"] = values["proposed_contact_role"] = ""
        elif values["party_type"] == "ORGANIZATION":
            if not values["organization_name"]: errors["organization_name"] = "La raison sociale est requise."
            values["first_name"] = values["last_name"] = ""
        for key, label in (("address_line1", "L’adresse est requise."), ("postal_code", "Le code postal est requis."), ("city", "La ville est requise."), ("country", "Le pays est requis.")):
            if not values[key]: errors[key] = label
        if values["email"] and "@" not in values["email"]:
            errors["email"] = "L’adresse e-mail est invalide."
        if errors:
            raise MasterDataValidationError(errors)
        return ClientDraft(**values)

    @staticmethod
    def _validate_site(draft: SiteDraft) -> SiteDraft:
        values = {key: _clean(value) if isinstance(value, str) else value for key, value in draft.__dict__.items()}
        errors = {key: label for key, label in (
            ("label", "Le nom du site est requis."), ("address_line1", "L’adresse est requise."),
            ("postal_code", "Le code postal est requis."), ("city", "La ville est requise."),
            ("country", "Le pays est requis."),
        ) if not values[key]}
        if errors:
            raise MasterDataValidationError(errors)
        return SiteDraft(**values)

    @staticmethod
    def _validate_equipment(draft: EquipmentDraft) -> EquipmentDraft:
        values = {key: _clean(value) if isinstance(value, str) else value for key, value in draft.__dict__.items()}
        errors = {}
        if not values["equipment_type"]: errors["equipment_type"] = "Le type est requis."
        if not values["location"]: errors["location"] = "La localisation est requise."
        if values["power_kw"] is not None:
            try:
                values["power_kw"] = float(values["power_kw"])
                if values["power_kw"] < 0: raise ValueError
            except (TypeError, ValueError):
                errors["power_kw"] = "La puissance doit être un nombre positif ou nul."
        if errors:
            raise MasterDataValidationError(errors)
        return EquipmentDraft(**values)

    @staticmethod
    def _persist(operation, *args) -> None:
        try:
            operation(*args)
        except LookupError as exc:
            raise MasterDataNotFoundError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise MasterDataPersistenceError() from exc
