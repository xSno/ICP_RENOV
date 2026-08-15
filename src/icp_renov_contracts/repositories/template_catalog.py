from __future__ import annotations

import json
import sqlite3

from ..database import DatabaseService
from ..domain import (
    ContractTemplate, ContractTemplateVersion, TemplateDefaults, TemplateOptionCatalogs,
    TemplateValidationMetadata, TemplateVersionStatus,
)


class TemplateCatalogRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    @staticmethod
    def _rows(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row

    def create_template(self, template: ContractTemplate) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO contract_templates(id,functional_name,document_kind,contract_type_code,created_at_utc) VALUES (?,?,?,?,?)",
                (template.id, template.functional_name, template.document_kind, template.contract_type_code, template.created_at_utc),
            )

    def create_version(self, version: ContractTemplateVersion) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO contract_template_versions(id,template_id,version,version_status,allowed_client_regimes_json,"
                "validation_metadata_json,defaults_json,option_catalogs_json,created_at_utc,updated_at_utc,"
                "source_relpath,source_hash,required_company_fields_json,required_intervention_fields_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (version.id, version.template_id, version.version, version.status.value,
                 json.dumps(version.allowed_client_regimes, ensure_ascii=False, separators=(",", ":")),
                 version.validation.to_json(), version.defaults.to_json(), version.catalogs.to_json(),
                 version.created_at_utc, version.updated_at_utc, version.source_relpath, version.source_hash,
                 json.dumps(version.required_company_fields, ensure_ascii=False, separators=(",", ":")),
                 json.dumps(version.required_intervention_fields, ensure_ascii=False, separators=(",", ":"))),
            )

    def get_version(self, version_id: str) -> ContractTemplateVersion | None:
        with self.database.connection() as connection:
            self._rows(connection)
            row = connection.execute(
                "SELECT v.*,t.functional_name,t.contract_type_code,t.document_kind FROM contract_template_versions v "
                "JOIN contract_templates t ON t.id=v.template_id WHERE v.id=?", (version_id,),
            ).fetchone()
            return self._version(row) if row else None

    def list_versions(self, contract_type_code: str, regime: str, available_only: bool = True) -> list[ContractTemplateVersion]:
        status = "AND v.version_status='AVAILABLE'" if available_only else ""
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                "SELECT v.*,t.functional_name,t.contract_type_code,t.document_kind FROM contract_template_versions v "
                "JOIN contract_templates t ON t.id=v.template_id WHERE t.contract_type_code=? " + status +
                " ORDER BY LOWER(t.functional_name),v.version,v.id", (contract_type_code,),
            ).fetchall()
            return [version for row in rows if regime in (version := self._version(row)).allowed_client_regimes]

    def list_versions_by_kind(self, document_kind: str, available_only: bool = True) -> list[ContractTemplateVersion]:
        status = "AND v.version_status='AVAILABLE'" if available_only else ""
        with self.database.connection() as connection:
            self._rows(connection)
            rows = connection.execute(
                "SELECT v.*,t.functional_name,t.contract_type_code,t.document_kind FROM contract_template_versions v "
                "JOIN contract_templates t ON t.id=v.template_id WHERE t.document_kind=? " + status +
                " ORDER BY LOWER(t.functional_name),v.version,v.id", (document_kind,),
            ).fetchall()
            return [self._version(row) for row in rows]

    def update_status(self, version_id: str, status: TemplateVersionStatus, now: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE contract_template_versions SET version_status=?,updated_at_utc=? WHERE id=?",
                (status.value, now, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)

    def update_generation_metadata(self, version_id: str, source_relpath: str, source_hash: str,
                                   required_company_fields: tuple[str, ...], required_intervention_fields: tuple[str, ...], now: str) -> None:
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM contract_documents WHERE template_version_id=?", (version_id,)).fetchone():
                raise ValueError("template version is immutable")
            cursor = connection.execute(
                "UPDATE contract_template_versions SET source_relpath=?,source_hash=?,required_company_fields_json=?,required_intervention_fields_json=?,updated_at_utc=? WHERE id=?",
                (source_relpath, source_hash, json.dumps(required_company_fields, ensure_ascii=False, separators=(",", ":")),
                 json.dumps(required_intervention_fields, ensure_ascii=False, separators=(",", ":")), now, version_id),
            )
            if cursor.rowcount != 1: raise LookupError(version_id)

    @staticmethod
    def _version(row: sqlite3.Row) -> ContractTemplateVersion:
        return ContractTemplateVersion(
            id=row["id"], template_id=row["template_id"], template_name=row["functional_name"],
            contract_type_code=row["contract_type_code"], version=row["version"],
            status=TemplateVersionStatus(row["version_status"]),
            allowed_client_regimes=tuple(json.loads(row["allowed_client_regimes_json"])),
            validation=TemplateValidationMetadata.from_json(row["validation_metadata_json"]),
            defaults=TemplateDefaults.from_json(row["defaults_json"]),
            catalogs=TemplateOptionCatalogs.from_json(row["option_catalogs_json"]),
            created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
            source_relpath=row["source_relpath"], source_hash=row["source_hash"],
            required_company_fields=tuple(json.loads(row["required_company_fields_json"])),
            document_kind=row["document_kind"],
            required_intervention_fields=tuple(json.loads(row["required_intervention_fields_json"])),
        )

    def source_in_use(self, version_id: str) -> bool:
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT 1 FROM contract_documents WHERE template_version_id=? LIMIT 1", (version_id,)
            ).fetchone() is not None
