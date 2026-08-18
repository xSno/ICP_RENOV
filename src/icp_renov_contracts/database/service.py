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
    Migration(
        7,
        (
            "ALTER TABLE contracts ADD COLUMN lifecycle_status TEXT CHECK (lifecycle_status IS NULL OR lifecycle_status IN ('SIGNED','ACTIVE'))",
            "DROP TRIGGER contract_documents_immutable_update",
            "ALTER TABLE contract_documents ADD COLUMN signed_pdf_path TEXT",
            "ALTER TABLE contract_documents ADD COLUMN signed_pdf_hash TEXT",
            "ALTER TABLE contract_documents ADD COLUMN signed_pdf_attached_at TEXT",
            """
            CREATE TRIGGER contract_documents_core_immutable_update
            BEFORE UPDATE ON contract_documents
            WHEN NEW.id IS NOT OLD.id
              OR NEW.contract_id IS NOT OLD.contract_id
              OR NEW.document_kind IS NOT OLD.document_kind
              OR NEW.revision_index IS NOT OLD.revision_index
              OR NEW.generated_at_utc IS NOT OLD.generated_at_utc
              OR NEW.template_version_id IS NOT OLD.template_version_id
              OR NEW.docx_relpath IS NOT OLD.docx_relpath
              OR NEW.pdf_relpath IS NOT OLD.pdf_relpath
              OR NEW.snapshot_json IS NOT OLD.snapshot_json
              OR NEW.docx_sha256 IS NOT OLD.docx_sha256
              OR NEW.pdf_sha256 IS NOT OLD.pdf_sha256
            BEGIN
                SELECT RAISE(ABORT, 'contract document core is immutable');
            END
            """,
            "CREATE UNIQUE INDEX idx_contract_one_signature ON contract_events(contract_id) WHERE type='SIGNATURE_RECORDED'",
            "CREATE UNIQUE INDEX idx_contract_one_activation ON contract_events(contract_id) WHERE type='ACTIVATED'",
            """
            CREATE TRIGGER contract_signature_required_fields
            BEFORE INSERT ON contract_events
            WHEN NEW.type='SIGNATURE_RECORDED' AND (NEW.document_id IS NULL OR NEW.effective_date IS NULL)
            BEGIN
                SELECT RAISE(ABORT, 'signature event fields required');
            END
            """,
            """
            CREATE TRIGGER contract_activation_required_fields
            BEFORE INSERT ON contract_events
            WHEN NEW.type='ACTIVATED' AND (NEW.effective_date IS NULL OR NEW.document_id IS NOT NULL)
            BEGIN
                SELECT RAISE(ABORT, 'activation event fields invalid');
            END
            """,
        ),
    ),
    Migration(
        8,
        (
            "ALTER TABLE contracts ADD COLUMN predecessor_contract_id TEXT REFERENCES contracts(id) ON DELETE RESTRICT CHECK (predecessor_contract_id IS NULL OR predecessor_contract_id != id)",
            "ALTER TABLE contracts ADD COLUMN terminal_status TEXT CHECK (terminal_status IS NULL OR terminal_status IN ('TERMINATED','EXPIRED','ABANDONED'))",
            "CREATE INDEX idx_contracts_predecessor ON contracts(predecessor_contract_id) WHERE predecessor_contract_id IS NOT NULL",
            "CREATE UNIQUE INDEX idx_contract_one_termination_schedule ON contract_events(contract_id) WHERE type='TERMINATION_SCHEDULED'",
            "CREATE UNIQUE INDEX idx_contract_one_terminated ON contract_events(contract_id) WHERE type='TERMINATED'",
            "CREATE UNIQUE INDEX idx_contract_one_expired ON contract_events(contract_id) WHERE type='EXPIRED'",
            "CREATE UNIQUE INDEX idx_contract_one_abandoned ON contract_events(contract_id) WHERE type='ABANDONED'",
            "CREATE UNIQUE INDEX idx_contract_renewal_period ON contract_events(contract_id,period_start,period_end) WHERE type='RENEWAL_CONFIRMED'",
            "CREATE UNIQUE INDEX idx_contract_nonrenewal_period ON contract_events(contract_id,period_start,period_end) WHERE type='RENEWAL_NOTICE_RECORDED'",
            """
            CREATE TRIGGER contract_s8_required_fields
            BEFORE INSERT ON contract_events
            WHEN (NEW.type='RENEWAL_CONFIRMED' AND (
                    NEW.period_start IS NULL OR NEW.period_end IS NULL OR NEW.renewal_annual_ht IS NULL
                    OR NEW.renewal_vat_rate IS NULL OR NEW.renewal_vat_amount IS NULL OR NEW.renewal_annual_ttc IS NULL
                    OR NEW.document_id IS NOT NULL))
              OR (NEW.type='RENEWAL_NOTICE_RECORDED' AND (NEW.effective_date IS NULL OR NEW.period_start IS NULL OR NEW.period_end IS NULL OR NEW.document_id IS NOT NULL))
              OR (NEW.type='TERMINATION_SCHEDULED' AND (NEW.effective_date IS NULL OR NEW.reason_text IS NULL OR trim(NEW.reason_text)='' OR NEW.document_id IS NOT NULL))
              OR (NEW.type IN ('TERMINATED','EXPIRED') AND (NEW.effective_date IS NULL OR NEW.document_id IS NOT NULL))
              OR (NEW.type='ABANDONED' AND NEW.document_id IS NOT NULL)
            BEGIN
                SELECT RAISE(ABORT, 'S8 event fields invalid');
            END
            """,
        ),
    ),
    Migration(
        9,
        (
            "ALTER TABLE contract_template_versions ADD COLUMN required_intervention_fields_json TEXT NOT NULL DEFAULT '[]'",
            "DROP TRIGGER contract_documents_core_immutable_update",
            "DROP TRIGGER contract_documents_immutable_delete",
            "DROP TRIGGER contract_events_document_scope_insert",
            "DROP TRIGGER contract_events_immutable_update",
            "DROP TRIGGER contract_events_immutable_delete",
            "DROP TRIGGER contract_signature_required_fields",
            "DROP TRIGGER contract_activation_required_fields",
            "DROP TRIGGER contract_s8_required_fields",
            "DROP INDEX idx_contract_documents_contract",
            "DROP INDEX idx_contract_events_history",
            "DROP INDEX idx_contract_events_document",
            "DROP INDEX idx_contract_one_signature",
            "DROP INDEX idx_contract_one_activation",
            "DROP INDEX idx_contract_one_termination_schedule",
            "DROP INDEX idx_contract_one_terminated",
            "DROP INDEX idx_contract_one_expired",
            "DROP INDEX idx_contract_one_abandoned",
            "DROP INDEX idx_contract_renewal_period",
            "DROP INDEX idx_contract_nonrenewal_period",
            "ALTER TABLE contract_events RENAME TO contract_events_s8",
            "ALTER TABLE contract_documents RENAME TO contract_documents_s8",
            """
            CREATE TABLE contract_documents (
                id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                document_kind TEXT NOT NULL CHECK (document_kind IN ('CONTRACT','INTERVENTION_SHEET')),
                revision_index INTEGER,
                generated_at_utc TEXT NOT NULL,
                template_version_id TEXT NOT NULL REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                docx_relpath TEXT NOT NULL,
                pdf_relpath TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                docx_sha256 TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                signed_pdf_path TEXT,
                signed_pdf_hash TEXT,
                signed_pdf_attached_at TEXT,
                CHECK (
                    (document_kind='CONTRACT' AND revision_index IS NOT NULL AND revision_index >= 1)
                    OR
                    (document_kind='INTERVENTION_SHEET' AND revision_index IS NULL
                     AND signed_pdf_path IS NULL AND signed_pdf_hash IS NULL AND signed_pdf_attached_at IS NULL)
                ),
                UNIQUE(contract_id, document_kind, revision_index),
                UNIQUE(docx_relpath),
                UNIQUE(pdf_relpath)
            )
            """,
            """
            INSERT INTO contract_documents
            SELECT id,contract_id,document_kind,revision_index,generated_at_utc,template_version_id,
                   docx_relpath,pdf_relpath,snapshot_json,docx_sha256,pdf_sha256,
                   signed_pdf_path,signed_pdf_hash,signed_pdf_attached_at
            FROM contract_documents_s8
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
            INSERT INTO contract_events
            SELECT id,contract_id,type,occurred_at,effective_date,document_id,period_start,period_end,
                   renewal_annual_ht,renewal_vat_rate,renewal_vat_amount,renewal_annual_ttc,
                   notification_date,reason_code,reason_text,note
            FROM contract_events_s8
            """,
            "DROP TABLE contract_events_s8",
            "DROP TABLE contract_documents_s8",
            "CREATE INDEX idx_contract_documents_contract ON contract_documents(contract_id, document_kind, generated_at_utc DESC)",
            "CREATE INDEX idx_contract_events_history ON contract_events(contract_id, occurred_at DESC, id DESC)",
            "CREATE INDEX idx_contract_events_document ON contract_events(document_id, type, effective_date DESC)",
            "CREATE UNIQUE INDEX idx_contract_one_signature ON contract_events(contract_id) WHERE type='SIGNATURE_RECORDED'",
            "CREATE UNIQUE INDEX idx_contract_one_activation ON contract_events(contract_id) WHERE type='ACTIVATED'",
            "CREATE UNIQUE INDEX idx_contract_one_termination_schedule ON contract_events(contract_id) WHERE type='TERMINATION_SCHEDULED'",
            "CREATE UNIQUE INDEX idx_contract_one_terminated ON contract_events(contract_id) WHERE type='TERMINATED'",
            "CREATE UNIQUE INDEX idx_contract_one_expired ON contract_events(contract_id) WHERE type='EXPIRED'",
            "CREATE UNIQUE INDEX idx_contract_one_abandoned ON contract_events(contract_id) WHERE type='ABANDONED'",
            "CREATE UNIQUE INDEX idx_contract_renewal_period ON contract_events(contract_id,period_start,period_end) WHERE type='RENEWAL_CONFIRMED'",
            "CREATE UNIQUE INDEX idx_contract_nonrenewal_period ON contract_events(contract_id,period_start,period_end) WHERE type='RENEWAL_NOTICE_RECORDED'",
            """
            CREATE TRIGGER contract_documents_core_immutable_update
            BEFORE UPDATE ON contract_documents
            WHEN NEW.id IS NOT OLD.id OR NEW.contract_id IS NOT OLD.contract_id
              OR NEW.document_kind IS NOT OLD.document_kind OR NEW.revision_index IS NOT OLD.revision_index
              OR NEW.generated_at_utc IS NOT OLD.generated_at_utc OR NEW.template_version_id IS NOT OLD.template_version_id
              OR NEW.docx_relpath IS NOT OLD.docx_relpath OR NEW.pdf_relpath IS NOT OLD.pdf_relpath
              OR NEW.snapshot_json IS NOT OLD.snapshot_json OR NEW.docx_sha256 IS NOT OLD.docx_sha256
              OR NEW.pdf_sha256 IS NOT OLD.pdf_sha256
            BEGIN SELECT RAISE(ABORT, 'contract document core is immutable'); END
            """,
            """
            CREATE TRIGGER contract_documents_immutable_delete
            BEFORE DELETE ON contract_documents BEGIN SELECT RAISE(ABORT, 'contract document is immutable'); END
            """,
            """
            CREATE TRIGGER contract_events_document_scope_insert
            BEFORE INSERT ON contract_events
            WHEN NEW.document_id IS NOT NULL AND (
                NOT EXISTS (SELECT 1 FROM contract_documents WHERE id=NEW.document_id AND contract_id=NEW.contract_id)
                OR (NEW.type IN ('CONTRACT_SENT','SIGNATURE_RECORDED') AND NOT EXISTS (
                    SELECT 1 FROM contract_documents WHERE id=NEW.document_id AND contract_id=NEW.contract_id AND document_kind='CONTRACT'
                ))
            ) BEGIN SELECT RAISE(ABORT, 'contract event document scope mismatch'); END
            """,
            "CREATE TRIGGER contract_events_immutable_update BEFORE UPDATE ON contract_events BEGIN SELECT RAISE(ABORT, 'contract event is immutable'); END",
            "CREATE TRIGGER contract_events_immutable_delete BEFORE DELETE ON contract_events BEGIN SELECT RAISE(ABORT, 'contract event is immutable'); END",
            """
            CREATE TRIGGER contract_signature_required_fields
            BEFORE INSERT ON contract_events
            WHEN NEW.type='SIGNATURE_RECORDED' AND (NEW.document_id IS NULL OR NEW.effective_date IS NULL)
            BEGIN SELECT RAISE(ABORT, 'signature event fields required'); END
            """,
            """
            CREATE TRIGGER contract_activation_required_fields
            BEFORE INSERT ON contract_events
            WHEN NEW.type='ACTIVATED' AND (NEW.effective_date IS NULL OR NEW.document_id IS NOT NULL)
            BEGIN SELECT RAISE(ABORT, 'activation event fields invalid'); END
            """,
            """
            CREATE TRIGGER contract_s8_required_fields
            BEFORE INSERT ON contract_events
            WHEN (NEW.type='RENEWAL_CONFIRMED' AND (
                    NEW.period_start IS NULL OR NEW.period_end IS NULL OR NEW.renewal_annual_ht IS NULL
                    OR NEW.renewal_vat_rate IS NULL OR NEW.renewal_vat_amount IS NULL OR NEW.renewal_annual_ttc IS NULL
                    OR NEW.document_id IS NOT NULL))
              OR (NEW.type='RENEWAL_NOTICE_RECORDED' AND (NEW.effective_date IS NULL OR NEW.period_start IS NULL OR NEW.period_end IS NULL OR NEW.document_id IS NOT NULL))
              OR (NEW.type='TERMINATION_SCHEDULED' AND (NEW.effective_date IS NULL OR NEW.reason_text IS NULL OR trim(NEW.reason_text)='' OR NEW.document_id IS NOT NULL))
              OR (NEW.type IN ('TERMINATED','EXPIRED') AND (NEW.effective_date IS NULL OR NEW.document_id IS NOT NULL))
              OR (NEW.type='ABANDONED' AND NEW.document_id IS NOT NULL)
            BEGIN SELECT RAISE(ABORT, 'S8 event fields invalid'); END
            """,
        ),
    ),
    Migration(
        10,
        (
            """
            CREATE TABLE company_settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1), legal_name TEXT NOT NULL DEFAULT '', trade_name TEXT NOT NULL DEFAULT '', legal_form TEXT NOT NULL DEFAULT '', share_capital TEXT NOT NULL DEFAULT '', siren TEXT NOT NULL DEFAULT '', siret TEXT NOT NULL DEFAULT '', registration_summary TEXT NOT NULL DEFAULT '', ape_code TEXT NOT NULL DEFAULT '', address_line1 TEXT NOT NULL DEFAULT '', address_line2 TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', correspondence_address TEXT NOT NULL DEFAULT '', vat_number TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', signatory_name TEXT NOT NULL DEFAULT '', signatory_role TEXT NOT NULL DEFAULT '', logo_relpath TEXT, logo_hash TEXT, insurer_name TEXT NOT NULL DEFAULT '', insurance_policy_number TEXT NOT NULL DEFAULT '', insurance_scope TEXT NOT NULL DEFAULT '', insurance_valid_until TEXT NOT NULL DEFAULT '', refrigerant_capacity_number TEXT NOT NULL DEFAULT '', refrigerant_capacity_body TEXT NOT NULL DEFAULT '', refrigerant_capacity_until TEXT NOT NULL DEFAULT '', refrigerant_partner_name TEXT NOT NULL DEFAULT '', mediator_name TEXT NOT NULL DEFAULT '', mediator_address TEXT NOT NULL DEFAULT '', mediator_website TEXT NOT NULL DEFAULT '', complaints_contact TEXT NOT NULL DEFAULT '', withdrawal_contact TEXT NOT NULL DEFAULT '', privacy_contact TEXT NOT NULL DEFAULT '', updated_at_utc TEXT NOT NULL
            )
            """,
            """INSERT INTO company_settings(singleton,trade_name,address_line1,postal_code,city,country,phone,email,siret,ape_code,updated_at_utc) VALUES (1,'ICP Renov','1138 boulevard Jean Moulin','83700','Saint-Raphaël','France','06 27 47 33 94','icprenov83@gmail.com','98948879600016','43.22A',CURRENT_TIMESTAMP)""",
        ),
    ),
    Migration(
        11,
        (
            "ALTER TABLE contract_template_versions ADD COLUMN previous_version_id TEXT REFERENCES contract_template_versions(id) ON DELETE RESTRICT",
            "ALTER TABLE contract_template_versions ADD COLUMN target_client_regimes_json TEXT NOT NULL DEFAULT '[]'",
            "CREATE INDEX idx_template_versions_previous ON contract_template_versions(previous_version_id) WHERE previous_version_id IS NOT NULL",
            """
            CREATE TABLE template_version_validation (
                version_id TEXT PRIMARY KEY REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                structure_status TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (structure_status IN ('NOT_RUN','PASS','FAIL')),
                structure_checked_at TEXT,
                structure_issues_json TEXT NOT NULL DEFAULT '[]',
                render_status TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (render_status IN ('NOT_RUN','PASS','FAIL')),
                render_tested_at TEXT,
                render_cases_json TEXT NOT NULL DEFAULT '[]',
                docx_status TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (docx_status IN ('NOT_RUN','PASS','FAIL')),
                pdf_status TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (pdf_status IN ('NOT_RUN','PASS','FAIL')),
                postflight_status TEXT NOT NULL DEFAULT 'NOT_RUN' CHECK (postflight_status IN ('NOT_RUN','PASS','FAIL')),
                equipment_coverage_json TEXT NOT NULL DEFAULT '[]',
                evidence_paths_json TEXT NOT NULL DEFAULT '[]',
                evidence_hashes_json TEXT NOT NULL DEFAULT '[]',
                visual_review_status TEXT NOT NULL DEFAULT 'TO_REVIEW' CHECK (visual_review_status IN ('TO_REVIEW','CONFIRMED','NOT_APPLICABLE')),
                context_review_status TEXT NOT NULL DEFAULT 'TO_REVIEW' CHECK (context_review_status IN ('TO_REVIEW','CONFIRMED','NOT_APPLICABLE')),
                external_content_status TEXT NOT NULL DEFAULT 'TO_REVIEW' CHECK (external_content_status IN ('NOT_APPLICABLE','TO_REVIEW','CONFIRMED','REJECTED')),
                external_validator TEXT NOT NULL DEFAULT '',
                external_validation_date TEXT,
                external_scope TEXT NOT NULL DEFAULT '',
                external_reference TEXT NOT NULL DEFAULT '',
                external_reservations TEXT NOT NULL DEFAULT '',
                last_validation_at_utc TEXT
            )
            """,
            "INSERT INTO template_version_validation(version_id) SELECT id FROM contract_template_versions",
            """
            CREATE TRIGGER template_version_validation_authority_insert
            AFTER INSERT ON contract_template_versions
            BEGIN
                INSERT INTO template_version_validation(version_id) VALUES (NEW.id);
            END
            """,
            """
            CREATE TABLE template_version_external_gates (
                version_id TEXT NOT NULL REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                gate_code TEXT NOT NULL CHECK (gate_code IN (
                    'LEGAL_BASE_CONTRACT_TERMS','LEGAL_CLIENT_REGIME_CLASSIFICATION','LEGAL_B2C_CONSUMER_TERMS',
                    'LEGAL_NON_PROFESSIONAL_TERMS','LEGAL_TACIT_RENEWAL','LEGAL_WITHDRAWAL_INFORMATION',
                    'LEGAL_WITHDRAWAL_FORM','LEGAL_EARLY_PERFORMANCE_REQUEST','LEGAL_ELECTRONIC_TERMINATION',
                    'LEGAL_ELECTRONIC_WITHDRAWAL','LEGAL_MEDIATOR','LEGAL_B2B_PAYMENT','LEGAL_B2B_JURISDICTION',
                    'LEGAL_PRICE_REVISION','LEGAL_REFRIGERANT_SCOPE','LEGAL_INSURANCE_REPRESENTATION',
                    'LEGAL_PRIVACY_NOTICE','LEGAL_SPECIAL_TERMS_PRIORITY'
                )),
                status TEXT NOT NULL CHECK (status IN ('NOT_APPLICABLE','TO_REVIEW','CONFIRMED','REJECTED')),
                reference TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(version_id, gate_code)
            )
            """,
            """
            CREATE TABLE template_version_regime_confirmations (
                version_id TEXT NOT NULL REFERENCES contract_template_versions(id) ON DELETE RESTRICT,
                regime TEXT NOT NULL CHECK (regime IN ('CONSUMER','NON_PROFESSIONAL','PROFESSIONAL')),
                reference TEXT NOT NULL CHECK (trim(reference) != ''),
                confirmed_at TEXT NOT NULL,
                PRIMARY KEY(version_id, regime)
            )
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
