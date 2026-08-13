from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientDraft:
    party_type: str
    first_name: str = ""
    last_name: str = ""
    organization_name: str = ""
    legal_form: str = ""
    siret: str = ""
    address_line1: str = ""
    address_line2: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = "France"
    billing_address: str = ""
    phone: str = ""
    email: str = ""
    internal_reference: str = ""
    internal_notes: str = ""
    proposed_contact_name: str = ""
    proposed_contact_role: str = ""


@dataclass(frozen=True)
class ClientMaster(ClientDraft):
    id: str = ""
    archived: bool = False
    created_at_utc: str = ""
    updated_at_utc: str = ""

    @property
    def display_name(self) -> str:
        if self.party_type == "PERSON":
            return " ".join(value for value in (self.first_name, self.last_name) if value)
        return self.organization_name

    @property
    def rendered_address(self) -> str:
        return ", ".join(value for value in (
            self.address_line1,
            self.address_line2,
            " ".join(value for value in (self.postal_code, self.city) if value),
            self.country,
        ) if value)


@dataclass(frozen=True)
class ClientSummary:
    client: ClientMaster
    site_count: int
    equipment_count: int


@dataclass(frozen=True)
class SiteDraft:
    label: str
    address_line1: str
    postal_code: str
    city: str
    country: str = "France"
    address_line2: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    internal_notes: str = ""


@dataclass(frozen=True)
class SiteMaster(SiteDraft):
    id: str = ""
    client_id: str = ""
    archived: bool = False
    created_at_utc: str = ""
    updated_at_utc: str = ""

    @property
    def rendered_address(self) -> str:
        return ", ".join(value for value in (
            self.address_line1,
            self.address_line2,
            " ".join(value for value in (self.postal_code, self.city) if value),
            self.country,
        ) if value)


@dataclass(frozen=True)
class EquipmentDraft:
    equipment_type: str
    location: str
    brand: str = ""
    model: str = ""
    serial_number: str = ""
    power_kw: float | None = None
    installation_date: str = ""
    internal_reference: str = ""
    internal_notes: str = ""


@dataclass(frozen=True)
class EquipmentMaster(EquipmentDraft):
    id: str = ""
    site_id: str = ""
    archived: bool = False
    created_at_utc: str = ""
    updated_at_utc: str = ""

    @property
    def display_name(self) -> str:
        return " — ".join(value for value in (self.equipment_type, self.brand, self.model) if value)
