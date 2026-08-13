from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContractEventType(str, Enum):
    CREATED = "CREATED"
    DOCUMENT_GENERATED = "DOCUMENT_GENERATED"
    CONTRACT_SENT = "CONTRACT_SENT"
    REOPENED_FOR_CORRECTION = "REOPENED_FOR_CORRECTION"
    SIGNATURE_RECORDED = "SIGNATURE_RECORDED"
    ACTIVATED = "ACTIVATED"
    RENEWAL_NOTICE_RECORDED = "RENEWAL_NOTICE_RECORDED"
    RENEWAL_CONFIRMED = "RENEWAL_CONFIRMED"
    TERMINATION_SCHEDULED = "TERMINATION_SCHEDULED"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"
    ADMIN_CORRECTION = "ADMIN_CORRECTION"


@dataclass(frozen=True)
class ContractEvent:
    id: str
    contract_id: str
    type: ContractEventType
    occurred_at: str
    effective_date: str | None = None
    document_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    renewal_annual_ht: str | None = None
    renewal_vat_rate: str | None = None
    renewal_vat_amount: str | None = None
    renewal_annual_ttc: str | None = None
    notification_date: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    note: str | None = None
