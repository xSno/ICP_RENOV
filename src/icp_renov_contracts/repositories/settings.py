from __future__ import annotations

import sqlite3

from ..database import DatabaseService
from ..domain import AlertSettings, AnnualNumberingPolicy, NumberFormatMode, NumberingSettings


class NumberingSettingsRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    @staticmethod
    def from_row(row: sqlite3.Row | None) -> NumberingSettings | None:
        if row is None:
            return None
        return NumberingSettings(
            NumberFormatMode(row["format_mode"]), row["prefix"], row["counter_width"],
            AnnualNumberingPolicy(row["annual_policy"]), row["series_start_counter"],
            row["next_counter"], row["counter_year"], row["configured_at_utc"], row["updated_at_utc"],
        )

    def get(self, connection: sqlite3.Connection | None = None) -> NumberingSettings | None:
        if connection is not None:
            connection.row_factory = sqlite3.Row
            return self.from_row(connection.execute("SELECT * FROM numbering_settings WHERE singleton=1").fetchone())
        with self.database.connection() as current:
            current.row_factory = sqlite3.Row
            return self.from_row(current.execute("SELECT * FROM numbering_settings WHERE singleton=1").fetchone())

    def save(self, settings: NumberingSettings) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO numbering_settings(
                    singleton,format_mode,prefix,counter_width,annual_policy,series_start_counter,
                    next_counter,counter_year,configured_at_utc,updated_at_utc
                ) VALUES (1,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    format_mode=excluded.format_mode,prefix=excluded.prefix,counter_width=excluded.counter_width,
                    annual_policy=excluded.annual_policy,series_start_counter=excluded.series_start_counter,
                    next_counter=excluded.next_counter,counter_year=excluded.counter_year,
                    updated_at_utc=excluded.updated_at_utc""",
                (settings.format_mode.value, settings.prefix, settings.counter_width,
                 settings.annual_policy.value, settings.series_start_counter, settings.next_counter,
                 settings.counter_year, settings.configured_at_utc, settings.updated_at_utc),
            )

    def has_official_numbers(self) -> bool:
        with self.database.connection() as connection:
            return connection.execute("SELECT 1 FROM contracts WHERE number IS NOT NULL LIMIT 1").fetchone() is not None


class AlertSettingsRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def get(self) -> AlertSettings:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM alert_settings WHERE singleton=1").fetchone()
            if row is None:
                return AlertSettings()
            return AlertSettings(row["default_internal_alert_days"], row["signature_followup_days"],
                                 row["backup_reminder_days"], row["updated_at_utc"])

    def save(self, settings: AlertSettings) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE alert_settings SET default_internal_alert_days=?,signature_followup_days=?,
                   backup_reminder_days=?,updated_at_utc=? WHERE singleton=1""",
                (settings.default_internal_alert_days, settings.signature_followup_days,
                 settings.backup_reminder_days, settings.updated_at_utc),
            )

