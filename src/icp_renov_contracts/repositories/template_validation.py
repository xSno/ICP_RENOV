from __future__ import annotations

import json
import sqlite3

from ..database import DatabaseService
from ..domain import (
    ExternalGateEvidence, ExternalGateStatus, RegimeConfirmation,
    ReviewEvidenceStatus, TemplateValidationRecord, ValidationCheckStatus,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class TemplateValidationRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def ensure(self, version_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO template_version_validation(version_id) VALUES (?)", (version_id,),
            )

    def get(self, version_id: str) -> TemplateValidationRecord:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM template_version_validation WHERE version_id=?", (version_id,),
            ).fetchone()
            if row is None: raise LookupError(version_id)
            gates = connection.execute(
                "SELECT gate_code,status,reference FROM template_version_external_gates WHERE version_id=? ORDER BY gate_code",
                (version_id,),
            ).fetchall()
            regimes = connection.execute(
                "SELECT regime,reference,confirmed_at FROM template_version_regime_confirmations WHERE version_id=? ORDER BY regime",
                (version_id,),
            ).fetchall()
        return TemplateValidationRecord(
            version_id=version_id,
            structure_status=ValidationCheckStatus(row["structure_status"]),
            structure_checked_at=row["structure_checked_at"],
            structure_issues=tuple(json.loads(row["structure_issues_json"])),
            render_status=ValidationCheckStatus(row["render_status"]),
            render_tested_at=row["render_tested_at"],
            render_cases=tuple(json.loads(row["render_cases_json"])),
            docx_status=ValidationCheckStatus(row["docx_status"]),
            pdf_status=ValidationCheckStatus(row["pdf_status"]),
            postflight_status=ValidationCheckStatus(row["postflight_status"]),
            equipment_coverage=tuple(json.loads(row["equipment_coverage_json"])),
            evidence_paths=tuple(json.loads(row["evidence_paths_json"])),
            evidence_hashes=tuple(json.loads(row["evidence_hashes_json"])),
            visual_review_status=ReviewEvidenceStatus(row["visual_review_status"]),
            context_review_status=ReviewEvidenceStatus(row["context_review_status"]),
            external_content_status=ExternalGateStatus(row["external_content_status"]),
            external_validator=row["external_validator"],
            external_validation_date=row["external_validation_date"],
            external_scope=row["external_scope"],
            external_reference=row["external_reference"],
            external_reservations=row["external_reservations"],
            last_validation_at_utc=row["last_validation_at_utc"],
            external_gates=tuple(ExternalGateEvidence(item["gate_code"], ExternalGateStatus(item["status"]), item["reference"]) for item in gates),
            regime_confirmations=tuple(RegimeConfirmation(item["regime"], item["reference"], item["confirmed_at"]) for item in regimes),
        )

    def save_structure(self, version_id: str, status: ValidationCheckStatus, checked_at: str,
                       issues: tuple[str, ...]) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE template_version_validation SET structure_status=?,structure_checked_at=?,structure_issues_json=?,last_validation_at_utc=? WHERE version_id=?",
                (status.value, checked_at, _json(issues), checked_at, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)

    def save_render(self, version_id: str, status: ValidationCheckStatus, tested_at: str,
                    cases: tuple[str, ...], docx_status: ValidationCheckStatus,
                    pdf_status: ValidationCheckStatus, postflight_status: ValidationCheckStatus,
                    equipment_coverage: tuple[int, ...], evidence_paths: tuple[str, ...],
                    evidence_hashes: tuple[str, ...]) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE template_version_validation SET render_status=?,render_tested_at=?,render_cases_json=?,docx_status=?,pdf_status=?,postflight_status=?,equipment_coverage_json=?,evidence_paths_json=?,evidence_hashes_json=?,last_validation_at_utc=? WHERE version_id=?",
                (status.value, tested_at, _json(cases), docx_status.value, pdf_status.value,
                 postflight_status.value, _json(equipment_coverage), _json(evidence_paths),
                 _json(evidence_hashes), tested_at, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)

    def set_visual_review(self, version_id: str, status: ReviewEvidenceStatus, now: str) -> None:
        self._update(version_id, "visual_review_status", status.value, now)

    def set_context_review(self, version_id: str, status: ReviewEvidenceStatus, now: str) -> None:
        self._update(version_id, "context_review_status", status.value, now)

    def set_external_content(self, version_id: str, status: ExternalGateStatus, validator: str,
                             validation_date: str | None, scope: str, reference: str,
                             reservations: str, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE template_version_validation SET external_content_status=?,external_validator=?,external_validation_date=?,external_scope=?,external_reference=?,external_reservations=?,last_validation_at_utc=? WHERE version_id=?",
                (status.value, validator, validation_date, scope, reference, reservations, now, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)

    def set_external_gate(self, version_id: str, code: str, status: ExternalGateStatus,
                          reference: str, now: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO template_version_external_gates(version_id,gate_code,status,reference,updated_at_utc) VALUES (?,?,?,?,?) "
                "ON CONFLICT(version_id,gate_code) DO UPDATE SET status=excluded.status,reference=excluded.reference,updated_at_utc=excluded.updated_at_utc",
                (version_id, code, status.value, reference, now),
            )

    def set_regime_confirmation(self, version_id: str, regime: str, reference: str,
                                confirmed_at: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO template_version_regime_confirmations(version_id,regime,reference,confirmed_at) VALUES (?,?,?,?) "
                "ON CONFLICT(version_id,regime) DO UPDATE SET reference=excluded.reference,confirmed_at=excluded.confirmed_at",
                (version_id, regime, reference, confirmed_at),
            )
            rows = connection.execute(
                "SELECT regime FROM template_version_regime_confirmations WHERE version_id=? ORDER BY regime", (version_id,),
            ).fetchall()
            connection.execute(
                "UPDATE contract_template_versions SET allowed_client_regimes_json=?,updated_at_utc=? WHERE id=?",
                (_json([row[0] for row in rows]), confirmed_at, version_id),
            )

    def _update(self, version_id: str, column: str, value: str, now: str) -> None:
        if column not in {"visual_review_status", "context_review_status"}: raise ValueError(column)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE template_version_validation SET {column}=?,last_validation_at_utc=? WHERE version_id=?",
                (value, now, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)
