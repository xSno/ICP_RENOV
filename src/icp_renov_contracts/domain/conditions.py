from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
import json


class TemplateVersionStatus(str, Enum):
    TO_VALIDATE = "TO_VALIDATE"
    AVAILABLE = "AVAILABLE"
    ARCHIVED = "ARCHIVED"


class ConclusionMode(str, Enum):
    IN_PREMISES = "IN_PREMISES"
    OFF_PREMISES = "OFF_PREMISES"
    DISTANCE_EMAIL = "DISTANCE_EMAIL"
    ONLINE_INTERFACE = "ONLINE_INTERFACE"
    OTHER_DISTANCE = "OTHER_DISTANCE"


class DurationMode(str, Enum):
    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"


class RefrigerantHandlingMode(str, Enum):
    IN_HOUSE_AUTHORIZED = "IN_HOUSE_AUTHORIZED"
    PARTNER = "PARTNER"
    EXCLUDED = "EXCLUDED"


class RenewalMode(str, Enum):
    NONE = "NONE"
    MANUAL = "MANUAL"
    TACIT = "TACIT"


class RenewalPriceRule(str, Enum):
    FIXED = "FIXED"
    NEW_PRICE_ON_RENEWAL = "NEW_PRICE_ON_RENEWAL"


INCLUDED_OPTIONS = frozenset({"DEEP_CLEANING", "DISINFECTION"})
LEGAL_BLOCKS = frozenset({"BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE", "BLOCK_ELECTRONIC_TERMINATION"})


def _json(value: object) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ControlledOption:
    code: str
    label: str


@dataclass(frozen=True)
class PaymentTermOption:
    code: str
    label: str
    requires_day_count: bool = False
    allows_custom_text: bool = False


@dataclass(frozen=True)
class ContextAuthorization:
    regime: str
    conclusion_mode: str
    blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateValidationMetadata:
    conclusion_required_regimes: tuple[str, ...] = ()
    context_authorizations: tuple[ContextAuthorization, ...] = ()

    def to_json(self) -> str:
        return _json(self)

    @classmethod
    def from_json(cls, value: str) -> "TemplateValidationMetadata":
        raw = json.loads(value)
        return cls(tuple(raw.get("conclusion_required_regimes", ())), tuple(
            ContextAuthorization(item["regime"], item["conclusion_mode"], tuple(item.get("blocks", ())))
            for item in raw.get("context_authorizations", ())
        ))

    def requires_conclusion(self, regime: str | None) -> bool:
        return regime is not None and regime in self.conclusion_required_regimes

    def blocks_for(self, regime: str | None, conclusion_mode: str | None) -> frozenset[str]:
        for context in self.context_authorizations:
            if context.regime == regime and context.conclusion_mode == conclusion_mode:
                return frozenset(context.blocks)
        return frozenset()


@dataclass(frozen=True)
class TemplateDefaults:
    visits_per_year: int | None = None
    refrigerant_handling_mode: str | None = None
    included_area: str = ""
    business_hours: str = ""
    travel_included: bool | None = None
    renewal_mode: str | None = None
    renewal_period_months: int | None = None
    internal_alert_days: int | None = None

    def to_json(self) -> str:
        return _json(self)

    @classmethod
    def from_json(cls, value: str) -> "TemplateDefaults":
        return cls(**json.loads(value))


@dataclass(frozen=True)
class TemplateOptionCatalogs:
    vat_rates: tuple[str, ...] = ()
    payment_terms: tuple[PaymentTermOption, ...] = ()
    payment_methods: tuple[ControlledOption, ...] = ()
    non_renewal_channels: tuple[ControlledOption, ...] = ()
    early_termination_reasons: tuple[ControlledOption, ...] = ()

    def to_json(self) -> str:
        return _json(self)

    @classmethod
    def from_json(cls, value: str) -> "TemplateOptionCatalogs":
        raw = json.loads(value)
        return cls(
            tuple(raw.get("vat_rates", ())),
            tuple(PaymentTermOption(**item) for item in raw.get("payment_terms", ())),
            tuple(ControlledOption(**item) for item in raw.get("payment_methods", ())),
            tuple(ControlledOption(**item) for item in raw.get("non_renewal_channels", ())),
            tuple(ControlledOption(**item) for item in raw.get("early_termination_reasons", ())),
        )


@dataclass(frozen=True)
class ContractTemplate:
    id: str
    functional_name: str
    document_kind: str
    contract_type_code: str
    created_at_utc: str


@dataclass(frozen=True)
class ContractTemplateVersion:
    id: str
    template_id: str
    template_name: str
    contract_type_code: str
    version: str
    status: TemplateVersionStatus
    allowed_client_regimes: tuple[str, ...]
    validation: TemplateValidationMetadata
    defaults: TemplateDefaults
    catalogs: TemplateOptionCatalogs
    created_at_utc: str
    updated_at_utc: str
    source_relpath: str | None = None
    source_hash: str | None = None
    required_company_fields: tuple[str, ...] = ()
    document_kind: str = "CONTRACT"

    @property
    def display_name(self) -> str:
        return f"{self.template_name} · {self.version}"


@dataclass(frozen=True)
class ContractConditions:
    conclusion_mode: str | None = None
    early_performance_requested: bool | None = None
    visits_per_year: int | None = None
    refrigerant_handling_mode: str | None = None
    included_area: str = ""
    business_hours: str = ""
    travel_included: bool | None = None
    priority_breakdown: bool | None = None
    priority_breakdown_delay: str | None = None
    included_options: tuple[str, ...] = ()
    additional_exclusions: str = ""
    issue_date: str | None = None
    start_date: str | None = None
    initial_duration_mode: str | None = None
    initial_duration_months: int | None = None
    initial_end_date: str | None = None
    signature_city: str = ""
    annual_ht: str | None = None
    vat_rate: str | None = None
    payment_terms_code: str | None = None
    payment_due_days: int | None = None
    payment_terms_custom_text: str = ""
    payment_methods: tuple[str, ...] = ()
    missed_appointment_fee: str | None = None
    renewal_mode: str | None = None
    renewal_period_months: int | None = None
    non_renewal_notice_days: int | None = None
    non_renewal_notice_channels: tuple[str, ...] = ()
    internal_alert_days: int | None = None
    renewal_price_rule: str | None = None
    early_termination_reason_codes: tuple[str, ...] = ()
    early_termination_custom_text: str = ""
    breach_cure_period_days: int | None = None
    special_terms: str = ""

    @property
    def resolved_end_date(self) -> str | None:
        if self.initial_duration_mode == DurationMode.CUSTOM.value:
            return self.initial_end_date
        if self.initial_duration_mode == DurationMode.STANDARD.value and self.start_date and self.initial_duration_months:
            return standard_end_date(self.start_date, self.initial_duration_months)
        return None

    @property
    def vat_amount(self) -> Decimal | None:
        if self.annual_ht is None or self.vat_rate is None:
            return None
        return (Decimal(self.annual_ht) * Decimal(self.vat_rate) / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def annual_ttc(self) -> Decimal | None:
        if self.annual_ht is None or self.vat_amount is None:
            return None
        return (Decimal(self.annual_ht) + self.vat_amount).quantize(Decimal("0.01"))

    def with_empty_defaults(self, defaults: TemplateDefaults) -> "ContractConditions":
        values = asdict(self)
        for field in fields(defaults):
            default = getattr(defaults, field.name)
            current = values[field.name]
            if default is not None and (current is None or current == ""):
                values[field.name] = default
        return ContractConditions(**values)


def standard_end_date(start_iso: str, months: int) -> str:
    start = date.fromisoformat(start_iso)
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    anniversary_day = min(start.day, monthrange(year, month)[1])
    return (date(year, month, anniversary_day) - timedelta(days=1)).isoformat()
