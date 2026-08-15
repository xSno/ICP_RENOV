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
        signed_pdf_path=row["signed_pdf_path"], signed_pdf_hash=row["signed_pdf_hash"],
        signed_pdf_attached_at=row["signed_pdf_attached_at"],
    )


class ContractDocumentRepository:
    def __init__(self, database: DatabaseService) -> None:
        self.database = database

    def list_for_contract(self, contract_id: str) -> tuple[ContractDocument, ...]:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM contract_documents WHERE contract_id=? ORDER BY revision_index,generated_at_utc,id", (contract_id,)
            ).fetchall()
            return tuple(_document(row) for row in rows)

    def list_for_contract_kind(self, contract_id: str, kind: DocumentKind) -> tuple[ContractDocument, ...]:
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            direction="DESC" if kind is DocumentKind.INTERVENTION_SHEET else "ASC"
            rows=connection.execute(
                f"SELECT * FROM contract_documents WHERE contract_id=? AND document_kind=? ORDER BY generated_at_utc {direction},id {direction}",
                (contract_id,kind.value),).fetchall()
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

    @staticmethod
    def set_signed_copy(connection: sqlite3.Connection, document_id: str, relpath: str,
                        digest: str, attached_at: str) -> None:
        cursor = connection.execute(
            "UPDATE contract_documents SET signed_pdf_path=?,signed_pdf_hash=?,signed_pdf_attached_at=? WHERE id=?",
            (relpath, digest, attached_at, document_id),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError("signed document missing")

    @staticmethod
    def relocate_signed_copy(connection: sqlite3.Connection, document_id: str, relpath: str) -> None:
        cursor = connection.execute(
            "UPDATE contract_documents SET signed_pdf_path=? WHERE id=? AND signed_pdf_hash IS NOT NULL",
            (relpath, document_id),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError("signed document metadata missing")
