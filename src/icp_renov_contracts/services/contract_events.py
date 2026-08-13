from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from ..database import DatabaseService
from ..documents.source_store import sha256_file
from ..documents.validation import DocumentGenerationError, validate_pdf
from ..domain import (
    ContractDocument, ContractEvent, ContractEventType, ContractStatus, DocumentKind, SignedCopyState,
)
from ..errors import ContractLifecycleError, ContractNotFoundError
from ..repositories import ContractDocumentRepository, ContractEventRepository
from .contracts import ContractService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _after(value: str) -> str:
    try:return (datetime.fromisoformat(value)+timedelta(microseconds=1)).isoformat()
    except ValueError:return value


class LocalBusinessDateProvider:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True)
class SignedContractAuthority:
    event: ContractEvent
    document: ContractDocument
    start_date: date


class FileOpener:
    def open(self, path: Path) -> None:
        os.startfile(path)  # type: ignore[attr-defined]


class ContractLifecycleService:
    def __init__(self, database: DatabaseService, contracts: ContractService,
                 documents: ContractDocumentRepository, events: ContractEventRepository,
                 workspace_root: Path, opener: FileOpener | None = None,
                 date_provider: LocalBusinessDateProvider | None = None,
                 now_provider: Callable[[], str] | None = None) -> None:
        self.database=database;self.contracts=contracts;self.documents=documents;self.events=events
        self.workspace_root=workspace_root.resolve();self.opener=opener or FileOpener()
        self.date_provider=date_provider or LocalBusinessDateProvider();self.now_provider=now_provider or _now

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
        if kind == "signed":
            if self.signed_copy_state(document) is not SignedCopyState.VALID:
                raise ContractLifecycleError("signed copy untrusted")
            relpath=document.signed_pdf_path
        else:
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
        now=self.now_provider();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.REOPENED_FOR_CORRECTION,now)
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT COALESCE(lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0]!=ContractStatus.TO_SIGN.value:raise ContractLifecycleError("status")
                if self._event_exists(connection,contract_id,ContractEventType.SIGNATURE_RECORDED):
                    raise ContractLifecycleError("signed")
                ContractEventRepository.insert(connection,event)
                cursor=connection.execute("UPDATE contracts SET generation_status=NULL,updated_at_utc=? WHERE id=? AND generation_status='TO_SIGN' AND lifecycle_status IS NULL",(now,contract_id))
                if cursor.rowcount!=1:raise ContractLifecycleError("status changed")
        except (ContractLifecycleError,ContractNotFoundError):raise
        except sqlite3.Error as exc:raise ContractLifecycleError("persistence") from exc

    def record_send(self, contract_id: str, document_id: str, effective_date: str, note: str = "") -> ContractEvent:
        try:date.fromisoformat(effective_date)
        except (TypeError,ValueError) as exc:raise ContractLifecycleError("invalid date") from exc
        document=self.documents.get(document_id)
        if document is None or document.contract_id!=contract_id or document.document_kind is not DocumentKind.CONTRACT:
            raise ContractLifecycleError("invalid document")
        event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.CONTRACT_SENT,self.now_provider(),effective_date,document_id,note=note.strip() or None)
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

    def signature_authority(self, contract_id: str) -> SignedContractAuthority | None:
        signature=next((event for event in self.events.list_for_contract(contract_id)
                        if event.type is ContractEventType.SIGNATURE_RECORDED),None)
        if signature is None:return None
        document=self.documents.get(signature.document_id or "")
        if document is None or document.contract_id!=contract_id or document.document_kind is not DocumentKind.CONTRACT:
            raise ContractLifecycleError("signature integrity")
        return SignedContractAuthority(signature,document,self._snapshot_start_date(document))

    def signature_date(self, contract_id: str) -> str | None:
        authority=self.signature_authority(contract_id)
        return authority.event.effective_date if authority else None

    def record_signature(self, contract_id: str, document_id: str, signature_date: str,
                         signed_pdf: Path | None = None) -> SignedContractAuthority:
        try:date.fromisoformat(signature_date)
        except (TypeError,ValueError) as exc:
            raise ContractLifecycleError("invalid signature date","Renseignez une date de signature valide.") from exc
        selected=self.documents.get(document_id)
        if selected is None or selected.contract_id!=contract_id or selected.document_kind is not DocumentKind.CONTRACT:
            raise ContractLifecycleError("invalid selected revision","La révision sélectionnée ne peut pas être enregistrée comme signée.")
        start_date=self._snapshot_start_date(selected)
        staged,digest=self._stage_pdf(signed_pdf) if signed_pdf is not None else (None,None)
        final:Path|None=None;moved=False;now=self.now_provider();target=ContractStatus.ACTIVE if start_date<=self.date_provider.today() else ContractStatus.SIGNED
        signature=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.SIGNATURE_RECORDED,now,signature_date,document_id)
        activation=(ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.ACTIVATED,_after(now),start_date.isoformat())
                    if target is ContractStatus.ACTIVE else None)
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT COALESCE(lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0]!=ContractStatus.TO_SIGN.value:raise ContractLifecycleError("status","Ce contrat ne peut pas être enregistré comme signé.")
                if self._event_exists(connection,contract_id,ContractEventType.SIGNATURE_RECORDED):
                    raise ContractLifecycleError("already signed","Une signature est déjà enregistrée pour ce contrat.")
                scope=connection.execute("SELECT contract_id,document_kind FROM contract_documents WHERE id=?",(document_id,)).fetchone()
                if scope is None or scope[0]!=contract_id or scope[1]!=DocumentKind.CONTRACT.value:
                    raise ContractLifecycleError("invalid selected revision","La révision sélectionnée ne peut pas être enregistrée comme signée.")
                if staged is not None:
                    final=self._new_signed_destination(selected)
                    if final.exists():raise ContractLifecycleError("collision","La signature n’a pas pu être enregistrée. Les données existantes sont conservées.")
                    final.parent.mkdir(parents=True,exist_ok=True);staged.replace(final);moved=True
                ContractEventRepository.insert(connection,signature)
                if activation is not None:ContractEventRepository.insert(connection,activation)
                if final is not None:
                    ContractDocumentRepository.set_signed_copy(connection,document_id,self._relative(final),digest,now)
                cursor=connection.execute(
                    "UPDATE contracts SET lifecycle_status=?,updated_at_utc=? WHERE id=? AND lifecycle_status IS NULL AND generation_status='TO_SIGN'",
                    (target.value,now,contract_id),
                )
                if cursor.rowcount!=1:raise sqlite3.IntegrityError("signature state changed")
        except (ContractLifecycleError,ContractNotFoundError):
            if moved and final is not None:self._unlink(final)
            raise
        except (OSError,sqlite3.Error) as exc:
            if moved and final is not None:self._unlink(final)
            raise ContractLifecycleError("signature persistence","La signature n’a pas pu être enregistrée. Les données existantes sont conservées.") from exc
        finally:
            if staged is not None:self._unlink(staged)
        return SignedContractAuthority(signature,self.documents.get(document_id),start_date)  # type: ignore[arg-type]

    def reconcile_due_activations(self, today: date | None = None) -> tuple[str, ...]:
        business_date=today or self.date_provider.today();failed=[]
        with self.database.connection() as connection:
            ids=[row[0] for row in connection.execute("SELECT id FROM contracts WHERE lifecycle_status='SIGNED'").fetchall()]
        for contract_id in ids:
            try:self._activate_if_due(contract_id,business_date)
            except ContractLifecycleError:failed.append(contract_id)
        return tuple(failed)

    def _activate_if_due(self,contract_id:str,today:date)->bool:
        authority=self.signature_authority(contract_id)
        if authority is None or authority.start_date>today:return False
        now=self.now_provider();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.ACTIVATED,now,authority.start_date.isoformat())
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT lifecycle_status FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0]==ContractStatus.ACTIVE.value:return False
                if row[0]!=ContractStatus.SIGNED.value:raise ContractLifecycleError("activation state")
                if self._event_exists(connection,contract_id,ContractEventType.ACTIVATED):
                    connection.execute("UPDATE contracts SET lifecycle_status='ACTIVE',updated_at_utc=? WHERE id=?",(now,contract_id));return False
                ContractEventRepository.insert(connection,event)
                cursor=connection.execute("UPDATE contracts SET lifecycle_status='ACTIVE',updated_at_utc=? WHERE id=? AND lifecycle_status='SIGNED'",(now,contract_id))
                if cursor.rowcount!=1:raise sqlite3.IntegrityError("activation state changed")
            return True
        except (ContractLifecycleError,ContractNotFoundError):raise
        except sqlite3.Error as exc:raise ContractLifecycleError("activation persistence") from exc

    def signed_copy_state(self, document: ContractDocument) -> SignedCopyState:
        if not document.signed_pdf_path or not document.signed_pdf_hash:return SignedCopyState.NONE
        path=self.resolve_document_path(document.signed_pdf_path)
        if path is None:return SignedCopyState.MISSING
        try:return SignedCopyState.VALID if sha256_file(path)==document.signed_pdf_hash else SignedCopyState.HASH_MISMATCH
        except OSError:return SignedCopyState.MISSING

    def add_signed_copy(self,contract_id:str,source:Path)->ContractDocument:
        authority=self.signature_authority(contract_id)
        if authority is None:raise ContractLifecycleError("unsigned")
        if authority.document.signed_pdf_path is not None:raise ContractLifecycleError("copy already recorded")
        return self._publish_copy(authority.document,source,False)

    def locate_signed_copy(self,contract_id:str,source:Path)->ContractDocument:
        authority=self.signature_authority(contract_id)
        if authority is None or not authority.document.signed_pdf_hash:raise ContractLifecycleError("no stored copy")
        staged,digest=self._stage_pdf(source)
        if digest!=authority.document.signed_pdf_hash:
            self._unlink(staged);raise ContractLifecycleError("wrong hash","Le fichier sélectionné ne correspond pas à la copie signée archivée.")
        return self._publish_staged(authority.document,staged,digest,True,authority.document.signed_pdf_attached_at)

    def replace_signed_copy(self,contract_id:str,source:Path)->ContractDocument:
        authority=self.signature_authority(contract_id)
        if authority is None or self.signed_copy_state(authority.document) is SignedCopyState.VALID:
            raise ContractLifecycleError("replacement unavailable")
        return self._publish_copy(authority.document,source,False)

    def _publish_copy(self,document:ContractDocument,source:Path,relocate:bool)->ContractDocument:
        staged,digest=self._stage_pdf(source)
        return self._publish_staged(document,staged,digest,relocate,None)

    def _publish_staged(self,document:ContractDocument,staged:Path,digest:str,relocate:bool,
                        attached_at:str|None)->ContractDocument:
        final:Path|None=None;moved=False;now=self.now_provider()
        try:
            with self.database.transaction() as connection:
                final=self._new_signed_destination(document)
                if final.exists():raise sqlite3.IntegrityError("signed copy collision")
                final.parent.mkdir(parents=True,exist_ok=True);staged.replace(final);moved=True
                if relocate:ContractDocumentRepository.relocate_signed_copy(connection,document.id,self._relative(final))
                else:ContractDocumentRepository.set_signed_copy(connection,document.id,self._relative(final),digest,attached_at or now)
        except (OSError,sqlite3.Error) as exc:
            if moved and final is not None:self._unlink(final)
            raise ContractLifecycleError("signed copy persistence","Le PDF signé n’a pas pu être archivé.") from exc
        finally:self._unlink(staged)
        return self.documents.get(document.id)  # type: ignore[return-value]

    def _stage_pdf(self,source:Path)->tuple[Path,str]:
        source=Path(source)
        try:validate_pdf(source)
        except (DocumentGenerationError,OSError) as exc:
            raise ContractLifecycleError("invalid signed pdf","Le PDF signé sélectionné n’est pas valide.") from exc
        temp_dir=self.workspace_root/"tmp"/"signed-copy";temp_dir.mkdir(parents=True,exist_ok=True)
        staged=temp_dir/f"{uuid.uuid4().hex}.pdf"
        try:shutil.copyfile(source,staged);validate_pdf(staged);return staged,sha256_file(staged)
        except (OSError,DocumentGenerationError) as exc:
            self._unlink(staged);raise ContractLifecycleError("invalid signed pdf","Le PDF signé sélectionné n’est pas valide.") from exc

    def _new_signed_destination(self,document:ContractDocument)->Path:
        return self.workspace_root/"documents"/"contracts"/document.contract_id/document.revision/"signed"/f"signed-{uuid.uuid4().hex}.pdf"

    def _relative(self,path:Path)->str:
        return path.resolve().relative_to(self.workspace_root).as_posix()

    @staticmethod
    def _unlink(path:Path)->None:
        try:path.unlink()
        except FileNotFoundError:pass

    @staticmethod
    def _snapshot_start_date(document:ContractDocument)->date:
        try:value=json.loads(document.snapshot_json)["contract"]["start_date"];return date.fromisoformat(value)
        except (KeyError,TypeError,ValueError,json.JSONDecodeError) as exc:
            raise ContractLifecycleError("signed snapshot start date","La date de prise d’effet de la révision signée est invalide.") from exc

    @staticmethod
    def _event_exists(connection:sqlite3.Connection,contract_id:str,event_type:ContractEventType)->bool:
        return connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type=?",(contract_id,event_type.value)).fetchone() is not None
