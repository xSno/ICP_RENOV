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
    Migration(
        3,
        (
            """
            CREATE TABLE contracts (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status = 'DRAFT'),
                type_code TEXT NOT NULL CHECK (type_code = 'CLIMATE_MAINTENANCE'),
                client_source_id TEXT REFERENCES clients(id) ON DELETE RESTRICT,
                site_source_id TEXT REFERENCES sites(id) ON DELETE RESTRICT,
                client_snapshot_json TEXT,
                site_snapshot_json TEXT,
                signatory_name TEXT NOT NULL DEFAULT '',
                signatory_role TEXT NOT NULL DEFAULT '',
                regime TEXT CHECK (regime IS NULL OR regime IN ('CONSUMER', 'NON_PROFESSIONAL', 'PROFESSIONAL')),
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                CHECK ((client_source_id IS NULL) = (client_snapshot_json IS NULL)),
                CHECK ((site_source_id IS NULL) = (site_snapshot_json IS NULL))
            )
            """,
            """
            CREATE TABLE contract_equipment_items (
                id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                source_equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
                position INTEGER NOT NULL CHECK (position >= 0),
                equipment_snapshot_json TEXT NOT NULL,
                observation TEXT NOT NULL DEFAULT '',
                UNIQUE(contract_id, source_equipment_id),
                UNIQUE(contract_id, position)
            )
            """,
            "CREATE INDEX idx_contracts_updated ON contracts(updated_at_utc DESC, id)",
            "CREATE INDEX idx_contract_equipment_order ON contract_equipment_items(contract_id, position)",
        ),
    ),
    Migration(
        4,
        (
            """
            CREATE TABLE contract_templates (
                id TEXT PRIMARY KEY,
                functional_name TEXT NOT NULL,
                document_kind TEXT NOT NULL,
                contract_type_code TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE contract_template_versions (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL REFERENCES contract_templates(id) ON DELETE RESTRICT,
                version TEXT NOT NULL,
                version_status TEXT NOT NULL CHECK (version_status IN ('TO_VALIDATE','AVAILABLE','ARCHIVED')),
                allowed_client_regimes_json TEXT NOT NULL,
                validation_metadata_json TEXT NOT NULL,
                defaults_json TEXT NOT NULL,
                option_catalogs_json TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                UNIQUE(template_id, version)
            )
            """,
            "ALTER TABLE contracts ADD COLUMN template_id TEXT REFERENCES contract_templates(id) ON DELETE RESTRICT",
            "ALTER TABLE contracts ADD COLUMN template_version_id TEXT REFERENCES contract_template_versions(id) ON DELETE RESTRICT",
            """
            CREATE TABLE contract_conditions (
                contract_id TEXT PRIMARY KEY REFERENCES contracts(id) ON DELETE RESTRICT,
                conclusion_mode TEXT CHECK (conclusion_mode IS NULL OR conclusion_mode IN ('IN_PREMISES','OFF_PREMISES','DISTANCE_EMAIL','ONLINE_INTERFACE','OTHER_DISTANCE')),
                early_performance_requested INTEGER CHECK (early_performance_requested IS NULL OR early_performance_requested IN (0,1)),
                visits_per_year INTEGER CHECK (visits_per_year IS NULL OR visits_per_year >= 1),
                refrigerant_handling_mode TEXT CHECK (refrigerant_handling_mode IS NULL OR refrigerant_handling_mode IN ('IN_HOUSE_AUTHORIZED','PARTNER','EXCLUDED')),
                included_area TEXT NOT NULL DEFAULT '',
                business_hours TEXT NOT NULL DEFAULT '',
                travel_included INTEGER CHECK (travel_included IS NULL OR travel_included IN (0,1)),
                priority_breakdown INTEGER CHECK (priority_breakdown IS NULL OR priority_breakdown IN (0,1)),
                priority_breakdown_delay TEXT,
                included_options_json TEXT NOT NULL DEFAULT '[]',
                additional_exclusions TEXT NOT NULL DEFAULT '',
                issue_date TEXT,
                start_date TEXT,
                initial_duration_mode TEXT CHECK (initial_duration_mode IS NULL OR initial_duration_mode IN ('STANDARD','CUSTOM')),
                initial_duration_months INTEGER CHECK (initial_duration_months IS NULL OR initial_duration_months >= 1),
                initial_end_date TEXT,
                signature_city TEXT NOT NULL DEFAULT '',
                annual_ht TEXT,
                vat_rate TEXT,
                payment_terms_code TEXT,
                payment_due_days INTEGER CHECK (payment_due_days IS NULL OR payment_due_days >= 0),
                payment_terms_custom_text TEXT NOT NULL DEFAULT '',
                payment_methods_json TEXT NOT NULL DEFAULT '[]',
                missed_appointment_fee TEXT,
                renewal_mode TEXT CHECK (renewal_mode IS NULL OR renewal_mode IN ('NONE','MANUAL','TACIT')),
                renewal_period_months INTEGER CHECK (renewal_period_months IS NULL OR renewal_period_months >= 1),
                non_renewal_notice_days INTEGER CHECK (non_renewal_notice_days IS NULL OR non_renewal_notice_days >= 0),
                non_renewal_notice_channels_json TEXT NOT NULL DEFAULT '[]',
                internal_alert_days INTEGER CHECK (internal_alert_days IS NULL OR internal_alert_days >= 0),
                renewal_price_rule TEXT CHECK (renewal_price_rule IS NULL OR renewal_price_rule IN ('FIXED','NEW_PRICE_ON_RENEWAL')),
                early_termination_reason_codes_json TEXT NOT NULL DEFAULT '[]',
                early_termination_custom_text TEXT NOT NULL DEFAULT '',
                breach_cure_period_days INTEGER CHECK (breach_cure_period_days IS NULL OR breach_cure_period_days >= 0),
                special_terms TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL
            )
            """,
            "INSERT INTO contract_conditions(contract_id,updated_at_utc) SELECT id,updated_at_utc FROM contracts",
            "CREATE INDEX idx_template_versions_selection ON contract_template_versions(version_status, template_id)",
        ),
    ),
    Migration(
        5,
        (
            "ALTER TABLE contracts ADD COLUMN number TEXT",
            "ALTER TABLE contracts ADD COLUMN generation_status TEXT CHECK (generation_status IS NULL OR generation_status='TO_SIGN')",
            "CREATE UNIQUE INDEX idx_contracts_number ON contracts(number) WHERE number IS NOT NULL",
            "ALTER TABLE contract_template_versions ADD COLUMN source_relpath TEXT",
            "ALTER TABLE contract_template_versions ADD COLUMN source_hash TEXT",
            "ALTER TABLE contract_template_versions ADD COLUMN required_company_fields_json TEXT NOT NULL DEFAULT '[]'",
            """
            CREATE TABLE contract_documents (
                id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                document_kind TEXT NOT NULL CHECK (document_kind = 'CONTRACT'),
                revision_index INTEGER NOT NULL CHECK (revision_index = 1),
                generated_at_utc TEXT NOT NULL,
                template_version_id TEXT NOT NULL REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                docx_relpath TEXT NOT NULL,
                pdf_relpath TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                docx_sha256 TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                UNIQUE(contract_id, document_kind, revision_index),
                UNIQUE(docx_relpath),
                UNIQUE(pdf_relpath)
            )
            """,
            "CREATE INDEX idx_contract_documents_contract ON contract_documents(contract_id, revision_index)",
            """
            CREATE TRIGGER contract_documents_immutable_update
            BEFORE UPDATE ON contract_documents BEGIN
                SELECT RAISE(ABORT, 'contract document is immutable');
            END
            """,
            """
            CREATE TRIGGER contract_documents_immutable_delete
            BEFORE DELETE ON contract_documents BEGIN
                SELECT RAISE(ABORT, 'contract document is immutable');
            END
            """,
        ),
    ),
    Migration(
        6,
        (
            "DROP TRIGGER contract_documents_immutable_update",
            "DROP TRIGGER contract_documents_immutable_delete",
            "ALTER TABLE contract_documents RENAME TO contract_documents_s5",
            """
            CREATE TABLE contract_documents (
                id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                document_kind TEXT NOT NULL CHECK (document_kind = 'CONTRACT'),
                revision_index INTEGER NOT NULL CHECK (revision_index >= 1),
                generated_at_utc TEXT NOT NULL,
                template_version_id TEXT NOT NULL REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                docx_relpath TEXT NOT NULL,
                pdf_relpath TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                docx_sha256 TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                UNIQUE(contract_id, document_kind, revision_index),
                UNIQUE(docx_relpath),
                UNIQUE(pdf_relpath)
            )
            """,
            """
            INSERT INTO contract_documents
            SELECT id,contract_id,document_kind,revision_index,generated_at_utc,template_version_id,
                   docx_relpath,pdf_relpath,snapshot_json,docx_sha256,pdf_sha256
            FROM contract_documents_s5
            """,
            "DROP TABLE contract_documents_s5",
            "CREATE INDEX idx_contract_documents_contract ON contract_documents(contract_id, revision_index)",
            """
            CREATE TRIGGER contract_documents_immutable_update
            BEFORE UPDATE ON contract_documents BEGIN
                SELECT RAISE(ABORT, 'contract document is immutable');
            END
            """,
            """
            CREATE TRIGGER contract_documents_immutable_delete
            BEFORE DELETE ON contract_documents BEGIN
                SELECT RAISE(ABORT, 'contract document is immutable');
            END
            """,
            """
            CREATE TABLE contract_events (
                id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                type TEXT NOT NULL CHECK (type IN (
                    'CREATED','DOCUMENT_GENERATED','CONTRACT_SENT','REOPENED_FOR_CORRECTION',
                    'SIGNATURE_RECORDED','ACTIVATED','RENEWAL_NOTICE_RECORDED','RENEWAL_CONFIRMED',
                    'TERMINATION_SCHEDULED','TERMINATED','EXPIRED','ABANDONED','ADMIN_CORRECTION'
                )),
                occurred_at TEXT NOT NULL,
                effective_date TEXT,
                document_id TEXT REFERENCES contract_documents(id) ON DELETE RESTRICT,
                period_start TEXT,
                period_end TEXT,
                renewal_annual_ht TEXT,
                renewal_vat_rate TEXT,
                renewal_vat_amount TEXT,
                renewal_annual_ttc TEXT,
                notification_date TEXT,
                reason_code TEXT,
                reason_text TEXT,
                note TEXT,
                CHECK (type NOT IN ('DOCUMENT_GENERATED','CONTRACT_SENT') OR document_id IS NOT NULL),
                CHECK (type != 'CONTRACT_SENT' OR effective_date IS NOT NULL)
            )
            """,
            """
            CREATE TRIGGER contract_events_document_scope_insert
            BEFORE INSERT ON contract_events
            WHEN NEW.document_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM contract_documents
                WHERE id=NEW.document_id AND contract_id=NEW.contract_id AND document_kind='CONTRACT'
            ) BEGIN
                SELECT RAISE(ABORT, 'contract event document scope mismatch');
            END
            """,
            "CREATE INDEX idx_contract_events_history ON contract_events(contract_id, occurred_at DESC, id DESC)",
            "CREATE INDEX idx_contract_events_document ON contract_events(document_id, type, effective_date DESC)",
            """
            CREATE TRIGGER contract_events_immutable_update
            BEFORE UPDATE ON contract_events BEGIN
                SELECT RAISE(ABORT, 'contract event is immutable');
            END
            """,
            """
            CREATE TRIGGER contract_events_immutable_delete
            BEFORE DELETE ON contract_events BEGIN
                SELECT RAISE(ABORT, 'contract event is immutable');
            END
            """,
            """
            INSERT INTO contract_events(id,contract_id,type,occurred_at)
            SELECT 'created:' || id,id,'CREATED',created_at_utc FROM contracts
            WHERE created_at_utc IS NOT NULL AND created_at_utc != ''
            """,
            """
            INSERT INTO contract_events(id,contract_id,type,occurred_at,document_id)
            SELECT 'document-generated:' || id,contract_id,'DOCUMENT_GENERATED',generated_at_utc,id
            FROM contract_documents WHERE document_kind='CONTRACT'
            """,
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
