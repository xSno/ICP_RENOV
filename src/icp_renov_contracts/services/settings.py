from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from collections.abc import Callable
import sqlite3

from ..documents.providers import ContractNumberAllocator
from ..domain import AlertSettings, AnnualNumberingPolicy, NumberFormatMode, NumberingSettings
from ..errors import NumberingCollisionError, NumberingValidationError
from ..repositories import AlertSettingsRepository, NumberingSettingsRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NumberingSettingsService:
    def __init__(self, repository: NumberingSettingsRepository,
                 date_provider=None) -> None:
        self.repository = repository
        if date_provider is None:
            from .contract_events import LocalBusinessDateProvider
            date_provider = LocalBusinessDateProvider()
        self.date_provider = date_provider

    def get(self) -> NumberingSettings | None:
        return self.repository.get()

    def has_official_numbers(self) -> bool:
        return self.repository.has_official_numbers()

    def effective_counter(self, settings: NumberingSettings) -> int:
        year = self.date_provider.today().year
        if AnnualNumberingPolicy(settings.annual_policy) is AnnualNumberingPolicy.RESET_ANNUALLY and settings.counter_year != year:
            return settings.series_start_counter
        return settings.next_counter

    @staticmethod
    def format_number(settings: NumberingSettings, year: int, counter: int) -> str:
        padded = str(counter).zfill(settings.counter_width)
        parts = ([settings.prefix] if settings.prefix else [])
        if NumberFormatMode(settings.format_mode).includes_year:
            parts.append(str(year))
        parts.append(padded)
        return "-".join(parts)

    def candidate(self, settings: NumberingSettings) -> str:
        self.validate(settings)
        return self.format_number(settings, self.date_provider.today().year, self.effective_counter(settings))

    def collides(self, candidate: str, connection: sqlite3.Connection | None = None) -> bool:
        if connection is not None:
            return connection.execute("SELECT 1 FROM contracts WHERE number=? LIMIT 1", (candidate,)).fetchone() is not None
        with self.repository.database.connection() as current:
            return current.execute("SELECT 1 FROM contracts WHERE number=? LIMIT 1", (candidate,)).fetchone() is not None

    def preview(self, settings: NumberingSettings | None = None) -> str | None:
        value = settings or self.get()
        if value is None:
            return None
        candidate = self.candidate(value)
        if self.collides(candidate):
            raise NumberingCollisionError()
        return candidate

    def validate(self, settings: NumberingSettings) -> None:
        try:
            mode = NumberFormatMode(settings.format_mode)
            policy = AnnualNumberingPolicy(settings.annual_policy)
            width = int(settings.counter_width)
            start = int(settings.series_start_counter)
            following = int(settings.next_counter)
        except (TypeError, ValueError) as exc:
            raise NumberingValidationError("controlled_value") from exc
        prefix = str(settings.prefix).strip()
        if not 1 <= width <= 8:
            raise NumberingValidationError("counter_width")
        if start < 1 or following < 1:
            raise NumberingValidationError("counter")
        if len(prefix) > 50 or any(character in prefix for character in '<>:"/\\|?*\r\n\t'):
            raise NumberingValidationError("prefix")
        if policy is AnnualNumberingPolicy.RESET_ANNUALLY and not mode.includes_year:
            raise NumberingValidationError("annual_year", "La remise à zéro annuelle nécessite un format incluant l’année.")
        candidate = self.format_number(replace(settings, format_mode=mode, annual_policy=policy, prefix=prefix,
                                               counter_width=width, series_start_counter=start, next_counter=following),
                                       self.date_provider.today().year, self.effective_counter(settings))
        if not candidate or len(candidate) > 120:
            raise NumberingValidationError("candidate")

    def save(self, settings: NumberingSettings) -> NumberingSettings:
        current = self.get()
        now = _now()
        prefix = str(settings.prefix).strip()
        value = replace(settings, format_mode=NumberFormatMode(settings.format_mode),
                        annual_policy=AnnualNumberingPolicy(settings.annual_policy), prefix=prefix,
                        counter_width=int(settings.counter_width), series_start_counter=int(settings.series_start_counter),
                        next_counter=int(settings.next_counter), configured_at_utc=current.configured_at_utc if current else now,
                        updated_at_utc=now)
        if current and self.has_official_numbers() and value.next_counter != current.next_counter:
            raise NumberingValidationError("next_counter_read_only", "Utilisez « Corriger le prochain numéro ».")
        if value.annual_policy is AnnualNumberingPolicy.RESET_ANNUALLY and current is None:
            value = replace(value, counter_year=self.date_provider.today().year)
        elif current is not None:
            value = replace(value, counter_year=current.counter_year)
        self.validate(value)
        candidate = self.candidate(value)
        if self.collides(candidate):
            raise NumberingCollisionError()
        self.repository.save(value)
        return self.get()  # type: ignore[return-value]

    def correct_next_counter(self, new_counter: int) -> NumberingSettings:
        current = self.get()
        if current is None or not self.has_official_numbers():
            raise NumberingValidationError("correction_unavailable")
        try:
            counter = int(new_counter)
        except (TypeError, ValueError) as exc:
            raise NumberingValidationError("counter") from exc
        year = self.date_provider.today().year
        value = replace(current, next_counter=counter,
                        counter_year=year if current.annual_policy is AnnualNumberingPolicy.RESET_ANNUALLY else current.counter_year,
                        updated_at_utc=_now())
        self.validate(value)
        if self.collides(self.candidate(value)):
            raise NumberingCollisionError()
        self.repository.save(value)
        return self.get()  # type: ignore[return-value]


class AlertSettingsService:
    def __init__(self, repository: AlertSettingsRepository) -> None:
        self.repository = repository

    def get(self) -> AlertSettings:
        return self.repository.get()

    def save(self, settings: AlertSettings) -> AlertSettings:
        values = []
        for value in (settings.default_internal_alert_days, settings.signature_followup_days,
                      settings.backup_reminder_days):
            if value is None:
                values.append(None)
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("alert_days") from exc
            if parsed < 0 or parsed > 3650:
                raise ValueError("alert_days")
            values.append(parsed)
        self.repository.save(AlertSettings(*values, updated_at_utc=_now()))
        return self.get()


class PersistedContractNumberAllocator(ContractNumberAllocator):
    def __init__(self, service: NumberingSettingsService, writable: Callable[[], bool] | None = None) -> None:
        self.service = service
        self.writable = writable or (lambda: True)
        self.unavailability_reason: str | None = None

    def available(self) -> bool:
        try:
            if not self.writable():
                self.unavailability_reason = "workspace"
                return False
            result = bool(self.service.preview())
            self.unavailability_reason = None if result else "unconfigured"
            return result
        except NumberingCollisionError:
            self.unavailability_reason = "collision"
            return False
        except (OSError, sqlite3.Error, NumberingValidationError):
            self.unavailability_reason = "unconfigured"
            return False

    def preview_next(self) -> str | None:
        try:
            return self.service.preview()
        except NumberingValidationError:
            return None

    def allocate(self, connection: sqlite3.Connection) -> str:
        settings = self.service.repository.get(connection)
        if settings is None:
            raise NumberingValidationError("unconfigured")
        candidate = self.service.format_number(settings, self.service.date_provider.today().year,
                                               self.service.effective_counter(settings))
        if self.service.collides(candidate, connection):
            raise NumberingCollisionError()
        counter = self.service.effective_counter(settings)
        year = self.service.date_provider.today().year
        cursor = connection.execute(
            "UPDATE numbering_settings SET next_counter=?,counter_year=?,updated_at_utc=? WHERE singleton=1 AND next_counter=?",
            (counter + 1, year if settings.annual_policy is AnnualNumberingPolicy.RESET_ANNUALLY else settings.counter_year,
             _now(), settings.next_counter),
        )
        if cursor.rowcount != 1:
            raise NumberingValidationError("state_changed")
        return candidate
