from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal


@dataclass(frozen=True)
class CompanySettings:
    legal_name: str = ""; trade_name: str = ""; legal_form: str = ""; share_capital: Decimal | None = None; siren: str = ""; siret: str = ""; registration_summary: str = ""; ape_code: str = ""
    address_line1: str = ""; address_line2: str = ""; postal_code: str = ""; city: str = ""; country: str = ""; correspondence_address: str = ""; vat_number: str = ""
    phone: str = ""; email: str = ""; signatory_name: str = ""; signatory_role: str = ""; logo_relpath: str | None = None; logo_hash: str | None = None
    insurer_name: str = ""; insurance_policy_number: str = ""; insurance_scope: str = ""; insurance_valid_until: str = ""; refrigerant_capacity_number: str = ""; refrigerant_capacity_body: str = ""; refrigerant_capacity_until: str = ""; refrigerant_partner_name: str = ""; mediator_name: str = ""; mediator_address: str = ""; mediator_website: str = ""; complaints_contact: str = ""; withdrawal_contact: str = ""; privacy_contact: str = ""; updated_at_utc: str = ""

    @property
    def display_name(self) -> str: return self.trade_name or self.legal_name
    @property
    def registered_address(self) -> str: return ", ".join(v for v in (self.address_line1, self.address_line2, " ".join(v for v in (self.postal_code, self.city) if v), self.country) if v)
    def document_mapping(self) -> dict[str, object]:
        result = {field.name: getattr(self, field.name) for field in fields(self) if field.name != "updated_at_utc"}
        result["logo"] = result.pop("logo_relpath") or ""
        if result["share_capital"] is not None: result["share_capital"] = format(result["share_capital"], "f")
        return result
