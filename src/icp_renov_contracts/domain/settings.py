from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NumberFormatMode(str, Enum):
    PREFIX_COUNTER = "PREFIX_COUNTER"
    PREFIX_YEAR_COUNTER = "PREFIX_YEAR_COUNTER"

    @property
    def includes_year(self) -> bool:
        return self is NumberFormatMode.PREFIX_YEAR_COUNTER


class AnnualNumberingPolicy(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    RESET_ANNUALLY = "RESET_ANNUALLY"


@dataclass(frozen=True)
class NumberingSettings:
    format_mode: NumberFormatMode
    prefix: str
    counter_width: int
    annual_policy: AnnualNumberingPolicy
    series_start_counter: int
    next_counter: int
    counter_year: int | None = None
    configured_at_utc: str = ""
    updated_at_utc: str = ""


@dataclass(frozen=True)
class AlertSettings:
    default_internal_alert_days: int | None = None
    signature_followup_days: int | None = None
    backup_reminder_days: int | None = None
    updated_at_utc: str = ""

