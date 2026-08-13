from __future__ import annotations

import sqlite3

from ..database import DatabaseService
from ..domain import ContractDocument, DocumentKind


def _document(row: sqlite3.Row) -> ContractDocument:
    return ContractDocument(
        id=row["id"], contract_id=row["contract_id"], document_kind=DocumentKind(row["document_kind"]),
        revision_index=row["revision_index"], generated_at_utc=row["generated_at_utc"],
        template_version_id=row["template_version_id"], docx_relpath=row["docx_relpath"],
        pdf_relpath=row["pdf_relpath"], snapshot_json=row["snapshot_json"],
        docx_sha256=row["docx_sha256"], pdf_sha256=row["pdf_sha256"],
    )


class ContractDocumentRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def list_for_contract(self, contract_id: str) -> tuple[ContractDocument, ...]:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM contract_documents WHERE contract_id=? ORDER BY revision_index", (contract_id,)
            ).fetchall()
            return tuple(_document(row) for row in rows)

    def get(self, document_id: str) -> ContractDocument | None:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM contract_documents WHERE id=?", (document_id,)).fetchone()
            return _document(row) if row else None

    @staticmethod
    def insert(connection: sqlite3.Connection, document: ContractDocument) -> None:
        connection.execute(
            "INSERT INTO contract_documents(id,contract_id,document_kind,revision_index,generated_at_utc,"
            "template_version_id,docx_relpath,pdf_relpath,snapshot_json,docx_sha256,pdf_sha256) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (document.id, document.contract_id, document.document_kind.value, document.revision_index,
             document.generated_at_utc, document.template_version_id, document.docx_relpath,
             document.pdf_relpath, document.snapshot_json, document.docx_sha256, document.pdf_sha256),
        )
