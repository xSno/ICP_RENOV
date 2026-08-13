from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from ..database import DatabaseService
from ..domain import ContractDocument, ContractEvent, ContractEventType, ContractStatus, DocumentKind
from ..errors import ContractLifecycleError, ContractNotFoundError
from ..repositories import ContractDocumentRepository, ContractEventRepository
from .contracts import ContractService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileOpener:
    def open(self, path: Path) -> None:
        os.startfile(path)  # type: ignore[attr-defined]


class ContractLifecycleService:
    def __init__(self, database: DatabaseService, contracts: ContractService,
                 documents: ContractDocumentRepository, events: ContractEventRepository,
                 workspace_root: Path, opener: FileOpener | None = None) -> None:
        self.database=database;self.contracts=contracts;self.documents=documents;self.events=events
        self.workspace_root=workspace_root.resolve();self.opener=opener or FileOpener()

    def revisions(self, contract_id: str) -> tuple[ContractDocument, ...]:
        self.contracts.get(contract_id)
        return tuple(reversed(self.documents.list_for_contract(contract_id)))

    def history(self, contract_id: str) -> tuple[ContractEvent, ...]:
        self.contracts.get(contract_id)
        return self.events.list_for_contract(contract_id)

    def resolve_document_path(self, relpath: str) -> Path | None:
        candidate=(self.workspace_root/Path(relpath)).resolve()
        try:candidate.relative_to(self.workspace_root)
        except ValueError:return None
        return candidate if candidate.is_file() else None

    def open_document(self, document_id: str, kind: str) -> None:
        document=self.documents.get(document_id)
        if document is None:raise ContractLifecycleError("document missing")
        relpath=document.docx_relpath if kind=="docx" else document.pdf_relpath if kind=="pdf" else None
        if relpath is None or (path:=self.resolve_document_path(relpath)) is None:
            raise ContractLifecycleError("file missing")
        self.opener.open(path)

    def open_contract_folder(self, contract_id: str) -> None:
        self.contracts.get(contract_id)
        path=(self.workspace_root/"documents"/"contracts"/contract_id).resolve()
        if not path.is_dir():raise ContractLifecycleError("folder missing")
        self.opener.open(path)

    def reopen_for_correction(self, contract_id: str) -> None:
        now=_now();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.REOPENED_FOR_CORRECTION,now)
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT COALESCE(generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0]!=ContractStatus.TO_SIGN.value:raise ContractLifecycleError("status")
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='SIGNATURE_RECORDED'",(contract_id,)).fetchone():
                    raise ContractLifecycleError("signed")
                ContractEventRepository.insert(connection,event)
                cursor=connection.execute("UPDATE contracts SET generation_status=NULL,updated_at_utc=? WHERE id=? AND generation_status='TO_SIGN'",(now,contract_id))
                if cursor.rowcount!=1:raise ContractLifecycleError("status changed")
        except (ContractLifecycleError,ContractNotFoundError):raise
        except sqlite3.Error as exc:raise ContractLifecycleError("persistence") from exc

    def record_send(self, contract_id: str, document_id: str, effective_date: str, note: str = "") -> ContractEvent:
        try:date.fromisoformat(effective_date)
        except (TypeError,ValueError) as exc:raise ContractLifecycleError("invalid date") from exc
        document=self.documents.get(document_id)
        if document is None or document.contract_id!=contract_id or document.document_kind is not DocumentKind.CONTRACT:
            raise ContractLifecycleError("invalid document")
        event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.CONTRACT_SENT,_now(),effective_date,document_id,note=note.strip() or None)
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT contract_id,document_kind FROM contract_documents WHERE id=?",(document_id,)).fetchone()
                if row is None or row[0]!=contract_id or row[1]!=DocumentKind.CONTRACT.value:raise ContractLifecycleError("invalid document")
                ContractEventRepository.insert(connection,event)
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("persistence") from exc
        return event

    def latest_send(self, document_id: str) -> ContractEvent | None:
        events=self.events.list_for_document(document_id,ContractEventType.CONTRACT_SENT)
        return max(events,key=lambda item:(item.effective_date or "",item.occurred_at,item.id),default=None)
