from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json

from .master_data import ClientMaster, EquipmentMaster, SiteMaster


class ContractStatus(str, Enum):
    DRAFT = "DRAFT"


class ContractType(str, Enum):
    CLIMATE_MAINTENANCE = "CLIMATE_MAINTENANCE"


class ContractRegime(str, Enum):
    CONSUMER = "CONSUMER"
    NON_PROFESSIONAL = "NON_PROFESSIONAL"
    PROFESSIONAL = "PROFESSIONAL"


def _dump(value: object) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ClientSnapshot:
    party_type: str
    first_name: str
    last_name: str
    organization_name: str
    legal_form: str
    siret: str
    address_line1: str
    address_line2: str
    postal_code: str
    city: str
    country: str
    billing_address: str
    phone: str
    email: str

    @classmethod
    def from_master(cls, value: ClientMaster) -> "ClientSnapshot":
        return cls(**{name: getattr(value, name) for name in cls.__dataclass_fields__})

    @property
    def display_name(self) -> str:
        if self.party_type == "PERSON":
            return " ".join(v for v in (self.first_name, self.last_name) if v)
        return self.organization_name

    def to_json(self) -> str:
        return _dump(self)

    @classmethod
    def from_json(cls, value: str) -> "ClientSnapshot":
        return cls(**json.loads(value))


@dataclass(frozen=True)
class SiteSnapshot:
    label: str
    address_line1: str
    address_line2: str
    postal_code: str
    city: str
    country: str
    contact_name: str
    contact_phone: str

    @classmethod
    def from_master(cls, value: SiteMaster) -> "SiteSnapshot":
        return cls(**{name: getattr(value, name) for name in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return _dump(self)

    @classmethod
    def from_json(cls, value: str) -> "SiteSnapshot":
        return cls(**json.loads(value))


@dataclass(frozen=True)
class EquipmentSnapshot:
    equipment_type: str
    brand: str
    model: str
    serial_number: str
    power_kw: float | None
    location: str
    installation_date: str

    @classmethod
    def from_master(cls, value: EquipmentMaster) -> "EquipmentSnapshot":
        return cls(**{name: getattr(value, name) for name in cls.__dataclass_fields__})

    @property
    def display_name(self) -> str:
        return " — ".join(v for v in (self.equipment_type, self.brand, self.model) if v)

    def to_json(self) -> str:
        return _dump(self)

    @classmethod
    def from_json(cls, value: str) -> "EquipmentSnapshot":
        return cls(**json.loads(value))


@dataclass(frozen=True)
class ContractEquipmentItem:
    id: str
    contract_id: str
    source_equipment_id: str
    position: int
    snapshot: EquipmentSnapshot
    observation: str = ""


@dataclass(frozen=True)
class Contract:
    id: str
    status: ContractStatus
    type_code: ContractType
    client_source_id: str | None
    site_source_id: str | None
    client_snapshot: ClientSnapshot | None
    site_snapshot: SiteSnapshot | None
    signatory_name: str
    signatory_role: str
    regime: ContractRegime | None
    template_id: str | None
    template_version_id: str | None
    equipment_items: tuple[ContractEquipmentItem, ...]
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True)
class ContractListItem:
    id: str
    status: ContractStatus
    client_name: str
    site_label: str
    updated_at_utc: str
