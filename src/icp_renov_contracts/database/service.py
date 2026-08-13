from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..errors import DatabaseInitializationError


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        2,
        (
            """
            CREATE TABLE clients (
                id TEXT PRIMARY KEY,
                party_type TEXT NOT NULL CHECK (party_type IN ('PERSON', 'ORGANIZATION')),
                first_name TEXT,
                last_name TEXT,
                organization_name TEXT,
                legal_form TEXT,
                siret TEXT,
                address_line1 TEXT NOT NULL,
                address_line2 TEXT,
                postal_code TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT 'France',
                billing_address TEXT,
                phone TEXT,
                email TEXT,
                internal_reference TEXT,
                internal_notes TEXT,
                proposed_contact_name TEXT,
                proposed_contact_role TEXT,
                archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                CHECK (
                    (party_type = 'PERSON' AND first_name IS NOT NULL AND last_name IS NOT NULL AND organization_name IS NULL)
                    OR
                    (party_type = 'ORGANIZATION' AND organization_name IS NOT NULL AND first_name IS NULL AND last_name IS NULL)
                )
            )
            """,
            """
            CREATE TABLE sites (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
                label TEXT NOT NULL,
                address_line1 TEXT NOT NULL,
                address_line2 TEXT,
                postal_code TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT 'France',
                contact_name TEXT,
                contact_phone TEXT,
                internal_notes TEXT,
                archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE equipment (
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
                equipment_type TEXT NOT NULL,
                brand TEXT,
                model TEXT,
                serial_number TEXT,
                power_kw REAL,
                location TEXT NOT NULL,
                installation_date TEXT,
                internal_reference TEXT,
                internal_notes TEXT,
                archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                CHECK (power_kw IS NULL OR power_kw >= 0)
            )
            """,
            "CREATE INDEX idx_clients_archived_name ON clients(archived, organization_name, last_name, first_name)",
            "CREATE INDEX idx_sites_client ON sites(client_id)",
            "CREATE INDEX idx_equipment_site ON equipment(site_id)",
        ),
    ),
)


class DatabaseService:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at_utc TEXT NOT NULL
                    )
                    """
                )
                current = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
                for migration in MIGRATIONS:
                    if migration.version <= current:
                        continue
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                        (migration.version, datetime.now(timezone.utc).isoformat()),
                    )
                connection.execute(f"PRAGMA user_version = {MIGRATIONS[-1].version}")
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseInitializationError(str(self.path)) from exc

    def schema_version(self) -> int:
        try:
            with self.connection() as connection:
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()
                return int(row[0])
        except sqlite3.Error as exc:
            raise DatabaseInitializationError(str(self.path)) from exc
