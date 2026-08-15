from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
    ClientSnapshot, Contract, ContractConditions, ContractDocument, ContractEquipmentItem, ContractEvent,
    ContractEventType, ContractRegime, ContractStatus, ContractType, DocumentKind, EquipmentSnapshot,
    SignedCopyState, SiteSnapshot, TemplateVersionStatus, standard_end_date,
)
from ..errors import ContractLifecycleError, ContractNotFoundError
from ..repositories import ContractDocumentRepository, ContractEventRepository
from ..repositories.conditions import CONDITION_COLUMNS, DB_COLUMN, ContractConditionsRepository
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


@dataclass(frozen=True)
class ContractPeriod:
    start: date
    end: date


@dataclass(frozen=True)
class ContractPrice:
    annual_ht: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    annual_ttc: Decimal


@dataclass(frozen=True)
class LifecycleProjection:
    authority: SignedContractAuthority
    period: ContractPeriod
    price: ContractPrice
    renewal_mode: str
    renewal_period_months: int | None
    renewal_price_rule: str | None
    next_attention_date: date | None
    non_renewal_deadline: date | None
    notice_channels: tuple[str, ...]
    non_renewal_event: ContractEvent | None
    pending_termination: ContractEvent | None

    @property
    def renewal_unresolved(self) -> bool:
        return self.renewal_mode == "TACIT" and self.non_renewal_event is None and self.pending_termination is None


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
        return tuple(sorted(self.documents.list_for_contract_kind(contract_id,DocumentKind.CONTRACT),key=lambda item:item.revision_index or 0,reverse=True))

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
                row=connection.execute("SELECT COALESCE(terminal_status,lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0]!=ContractStatus.TO_SIGN.value:raise ContractLifecycleError("status")
                if self._event_exists(connection,contract_id,ContractEventType.SIGNATURE_RECORDED):
                    raise ContractLifecycleError("signed")
                ContractEventRepository.insert(connection,event)
                cursor=connection.execute("UPDATE contracts SET generation_status=NULL,updated_at_utc=? WHERE id=? AND generation_status='TO_SIGN' AND lifecycle_status IS NULL AND terminal_status IS NULL",(now,contract_id))
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

    def lifecycle_projection(self, contract_id: str) -> LifecycleProjection:
        authority=self.signature_authority(contract_id)
        if authority is None:
            raise ContractLifecycleError("signed authority missing","Les informations du contrat signé ne peuvent pas être déterminées.")
        try:
            snapshot=authority.document.snapshot;contract=snapshot["contract"];pricing=snapshot["pricing"]
            initial=ContractPeriod(date.fromisoformat(contract["start_date"]),date.fromisoformat(contract["initial_end_date"]))
            initial_price=self._price(pricing["annual_ht"],pricing["vat_rate"])
            renewal_mode=str(contract["renewal_mode"]);renewal_months=self._positive_int(contract.get("renewal_period_months"))
            alert_days=self._nonnegative_int(contract.get("internal_alert_days"));notice_days=self._nonnegative_int(contract.get("non_renewal_notice_days"))
            notice_channels=tuple(contract.get("non_renewal_notice_channels") or ())
            price_rule=pricing.get("renewal_price_rule")
        except (KeyError,TypeError,ValueError,InvalidOperation) as exc:
            raise ContractLifecycleError("signed lifecycle integrity","Les informations du contrat signé ne peuvent pas être déterminées.") from exc
        renewals=sorted((event for event in self.events.list_for_contract(contract_id) if event.type is ContractEventType.RENEWAL_CONFIRMED),key=lambda e:(e.period_start or "",e.occurred_at,e.id))
        period=initial;price=initial_price
        for event in renewals:
            try:
                candidate=ContractPeriod(date.fromisoformat(event.period_start or ""),date.fromisoformat(event.period_end or ""))
                candidate_price=self._price_event(event)
            except (TypeError,ValueError,InvalidOperation) as exc:
                raise ContractLifecycleError("renewal history integrity","Les informations du contrat signé ne peuvent pas être déterminées.") from exc
            if candidate.start!=period.end+timedelta(days=1) or candidate.end<candidate.start:
                raise ContractLifecycleError("renewal history integrity","Les informations du contrat signé ne peuvent pas être déterminées.")
            period=candidate;price=candidate_price
        notices=[event for event in self.events.list_for_contract(contract_id) if event.type is ContractEventType.RENEWAL_NOTICE_RECORDED and event.period_start==period.start.isoformat() and event.period_end==period.end.isoformat()]
        terminations=[event for event in self.events.list_for_contract(contract_id) if event.type is ContractEventType.TERMINATION_SCHEDULED]
        return LifecycleProjection(authority,period,price,renewal_mode,renewal_months,price_rule,
            period.end-timedelta(days=alert_days) if renewal_mode in {"MANUAL","TACIT"} and alert_days is not None else None,
            period.end-timedelta(days=notice_days) if renewal_mode=="TACIT" and notice_days is not None else None,
            notice_channels,max(notices,key=lambda e:(e.occurred_at,e.id),default=None),
            max(terminations,key=lambda e:(e.occurred_at,e.id),default=None))

    def confirm_renewal(self,contract_id:str,annual_ht:str|None=None,vat_rate:str|None=None,note:str="")->ContractEvent:
        contract=self.contracts.get(contract_id);projection=self.lifecycle_projection(contract_id)
        if contract.status is not ContractStatus.ACTIVE or projection.renewal_mode!="TACIT" or not projection.renewal_period_months:
            raise ContractLifecycleError("renewal unavailable","Cette reconduction ne peut pas être enregistrée.")
        if projection.non_renewal_event or projection.pending_termination:
            raise ContractLifecycleError("renewal resolved","Cette reconduction ne peut pas être enregistrée.")
        start=projection.period.end+timedelta(days=1);end=date.fromisoformat(standard_end_date(start.isoformat(),projection.renewal_period_months))
        if projection.renewal_price_rule=="FIXED":price=projection.price
        elif projection.renewal_price_rule=="NEW_PRICE_ON_RENEWAL":
            if annual_ht in (None,"") or vat_rate in (None,""):raise ContractLifecycleError("renewal price required","Renseignez le nouveau prix annuel HT et le taux de TVA.")
            try:price=self._price(annual_ht,vat_rate,rate_is_percent=True)
            except (ValueError,InvalidOperation) as exc:raise ContractLifecycleError("invalid renewal price","Renseignez un prix et un taux de TVA valides.") from exc
            allowed=self._allowed_vat_rates(projection.authority.document)
            if format(price.vat_rate,"f") not in allowed:raise ContractLifecycleError("invalid renewal vat","Choisissez un taux de TVA configuré.")
        else:raise ContractLifecycleError("renewal price rule","Cette reconduction ne peut pas être enregistrée.")
        now=self.now_provider();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.RENEWAL_CONFIRMED,now,
            period_start=start.isoformat(),period_end=end.isoformat(),renewal_annual_ht=self._decimal_text(price.annual_ht),
            renewal_vat_rate=self._decimal_text(price.vat_rate),renewal_vat_amount=self._decimal_text(price.vat_amount),
            renewal_annual_ttc=self._decimal_text(price.annual_ttc),note=note.strip() or None)
        try:
            with self.database.transaction() as connection:
                self._assert_active_tacit(connection,contract_id,projection.period)
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='TERMINATION_SCHEDULED'",(contract_id,)).fetchone():raise ContractLifecycleError("termination pending")
                ContractEventRepository.insert(connection,event)
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("renewal persistence","La reconduction n’a pas pu être enregistrée. Les données existantes sont conservées.") from exc
        return event

    def record_non_renewal(self,contract_id:str,notification_date:str,note:str="")->ContractEvent:
        try:date.fromisoformat(notification_date)
        except (TypeError,ValueError) as exc:raise ContractLifecycleError("invalid notification date","Renseignez une date de notification valide.") from exc
        contract=self.contracts.get(contract_id);projection=self.lifecycle_projection(contract_id)
        if contract.status is not ContractStatus.ACTIVE or projection.renewal_mode!="TACIT" or projection.non_renewal_event or projection.pending_termination:
            raise ContractLifecycleError("nonrenewal unavailable","Le non-renouvellement ne peut pas être enregistré.")
        event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.RENEWAL_NOTICE_RECORDED,self.now_provider(),notification_date,
            period_start=projection.period.start.isoformat(),period_end=projection.period.end.isoformat(),note=note.strip() or None)
        try:
            with self.database.transaction() as connection:
                self._assert_active_tacit(connection,contract_id,projection.period);ContractEventRepository.insert(connection,event)
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("nonrenewal persistence","Le non-renouvellement n’a pas pu être enregistré.") from exc
        return event

    def schedule_termination(self,contract_id:str,effective_date:str,reason_text:str,notification_date:str|None=None,note:str="")->ContractEvent:
        try:effective=date.fromisoformat(effective_date);notification=date.fromisoformat(notification_date) if notification_date else None
        except (TypeError,ValueError) as exc:raise ContractLifecycleError("invalid termination date","Renseignez des dates valides.") from exc
        reason=reason_text.strip()
        if not reason:raise ContractLifecycleError("termination reason required","Renseignez le motif de la résiliation.")
        contract=self.contracts.get(contract_id);projection=self.lifecycle_projection(contract_id)
        if contract.status not in {ContractStatus.SIGNED,ContractStatus.ACTIVE} or projection.pending_termination or effective>projection.period.end:
            raise ContractLifecycleError("termination unavailable","La résiliation ne peut pas être programmée pour cette date.")
        now=self.now_provider();scheduled=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.TERMINATION_SCHEDULED,now,effective.isoformat(),notification_date=notification.isoformat() if notification else None,reason_text=reason,note=note.strip() or None)
        terminated=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.TERMINATED,_after(now),effective.isoformat()) if effective<=self.date_provider.today() else None
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT COALESCE(terminal_status,lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None or row[0] not in {"SIGNED","ACTIVE"}:raise ContractLifecycleError("termination state")
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='TERMINATION_SCHEDULED'",(contract_id,)).fetchone():raise ContractLifecycleError("termination pending")
                ContractEventRepository.insert(connection,scheduled)
                if terminated:
                    ContractEventRepository.insert(connection,terminated);connection.execute("UPDATE contracts SET terminal_status='TERMINATED',updated_at_utc=? WHERE id=?",(_after(now),contract_id))
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("termination persistence","La résiliation n’a pas pu être enregistrée. Les données existantes sont conservées.") from exc
        return scheduled

    def abandon(self,contract_id:str)->ContractEvent:
        contract=self.contracts.get(contract_id)
        if contract.status not in {ContractStatus.DRAFT,ContractStatus.TO_SIGN}:raise ContractLifecycleError("abandon unavailable","Ce contrat ne peut pas être abandonné.")
        now=self.now_provider();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.ABANDONED,now)
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT COALESCE(terminal_status,lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None or row[0] not in {"DRAFT","TO_SIGN"} or self._event_exists(connection,contract_id,ContractEventType.SIGNATURE_RECORDED):raise ContractLifecycleError("abandon state")
                ContractEventRepository.insert(connection,event);connection.execute("UPDATE contracts SET terminal_status='ABANDONED',updated_at_utc=? WHERE id=?",(now,contract_id))
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("abandon persistence","Le contrat n’a pas pu être abandonné. Les données existantes sont conservées.") from exc
        return event

    def create_linked_draft(self,contract_id:str)->Contract:
        predecessor=self.contracts.get(contract_id);projection=self.lifecycle_projection(contract_id)
        if predecessor.status is not ContractStatus.ACTIVE:raise ContractLifecycleError("linked draft unavailable","Un nouveau contrat lié ne peut pas être préparé dans cet état.")
        snapshot=projection.authority.document.snapshot;contract_data=snapshot.get("contract",{});service=snapshot.get("service",{});pricing=snapshot.get("pricing",{})
        try:
            client=self._client_snapshot(snapshot["client"]);site=self._site_snapshot(snapshot["site"])
            equipment_data=tuple(sorted(contract_data["equipment_items"],key=lambda item:item["position"]))
            start=(projection.period.end+timedelta(days=1)).isoformat();months=projection.renewal_period_months or self._positive_int(contract_data.get("initial_duration_months"))
            if not months:raise ValueError("duration")
            end=standard_end_date(start,months);price=projection.price
            conditions=ContractConditions(
                conclusion_mode=contract_data.get("conclusion_mode"),early_performance_requested=contract_data.get("early_performance_requested"),
                visits_per_year=service.get("visits_per_year"),refrigerant_handling_mode=service.get("refrigerant_handling_mode"),
                included_area=service.get("included_area") or "",business_hours=service.get("business_hours") or "",travel_included=service.get("travel_included"),
                priority_breakdown=service.get("priority_breakdown"),priority_breakdown_delay=service.get("priority_breakdown_delay"),included_options=tuple(service.get("included_options") or ()),
                additional_exclusions=service.get("additional_exclusions") or "",issue_date=self.date_provider.today().isoformat(),start_date=start,
                initial_duration_mode="STANDARD",initial_duration_months=months,initial_end_date=end,signature_city=contract_data.get("signature_city") or "",
                annual_ht=self._decimal_text(price.annual_ht),vat_rate=self._decimal_text(price.vat_rate),payment_terms_code=pricing.get("payment_terms_code"),
                payment_due_days=pricing.get("payment_due_days"),payment_terms_custom_text=pricing.get("payment_terms_custom_text") or "",payment_methods=tuple(pricing.get("payment_methods") or ()),
                missed_appointment_fee=pricing.get("missed_appointment_fee"),renewal_mode=contract_data.get("renewal_mode"),renewal_period_months=contract_data.get("renewal_period_months"),
                non_renewal_notice_days=contract_data.get("non_renewal_notice_days"),non_renewal_notice_channels=tuple(contract_data.get("non_renewal_notice_channels") or ()),
                internal_alert_days=contract_data.get("internal_alert_days"),renewal_price_rule=pricing.get("renewal_price_rule"),
                breach_cure_period_days=contract_data.get("breach_cure_period_days"),special_terms=contract_data.get("special_terms") or "",
            )
        except (KeyError,TypeError,ValueError,InvalidOperation) as exc:raise ContractLifecycleError("linked draft integrity","Les informations du contrat signé ne peuvent pas être déterminées.") from exc
        version_id=projection.authority.document.template_version_id;template_id=None
        with self.database.connection() as connection:
            version=connection.execute("SELECT v.template_id,v.version_status,t.contract_type_code,v.allowed_client_regimes_json FROM contract_template_versions v JOIN contract_templates t ON t.id=v.template_id WHERE v.id=?",(version_id,)).fetchone()
            if version and version[1]==TemplateVersionStatus.AVAILABLE.value and version[2]==predecessor.type_code.value and predecessor.regime and predecessor.regime.value in json.loads(version[3]):template_id=version[0]
            else:version_id=None
        new_id=str(uuid.uuid4());now=self.now_provider();created=ContractEvent(str(uuid.uuid4()),new_id,ContractEventType.CREATED,now)
        assignments=",".join(DB_COLUMN[name] for name in CONDITION_COLUMNS);placeholders=",".join("?" for _ in CONDITION_COLUMNS)
        condition_values=tuple(ContractConditionsRepository._value(name,getattr(conditions,name)) for name in CONDITION_COLUMNS)
        live_by_position={item.position:item.source_equipment_id for item in predecessor.equipment_items}
        try:
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO contracts(id,status,type_code,client_source_id,site_source_id,client_snapshot_json,site_snapshot_json,signatory_name,signatory_role,regime,template_id,template_version_id,created_at_utc,updated_at_utc,predecessor_contract_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id,"DRAFT",predecessor.type_code.value,predecessor.client_source_id,predecessor.site_source_id,client.to_json(),site.to_json(),
                     snapshot["client"].get("representative_name") or predecessor.signatory_name,snapshot["client"].get("representative_role") or predecessor.signatory_role,
                     predecessor.regime.value if predecessor.regime else None,template_id,version_id,now,now,contract_id))
                connection.execute(f"INSERT INTO contract_conditions(contract_id,{assignments},updated_at_utc) VALUES (?,{placeholders},?)",(new_id,*condition_values,now))
                for position,item in enumerate(equipment_data):
                    source_id=live_by_position.get(int(item.get("position",position)))
                    if source_id is None:raise ContractLifecycleError("equipment source integrity","Les informations du contrat signé ne peuvent pas être déterminées.")
                    equipment=self._equipment_snapshot(item)
                    connection.execute("INSERT INTO contract_equipment_items(id,contract_id,source_equipment_id,position,equipment_snapshot_json,observation) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4()),new_id,source_id,position,equipment.to_json(),str(item.get("observations") or "")))
                ContractEventRepository.insert(connection,created)
        except ContractLifecycleError:raise
        except sqlite3.Error as exc:raise ContractLifecycleError("linked draft persistence","Le brouillon lié n’a pas pu être créé. Les données existantes sont conservées.") from exc
        return self.contracts.get(new_id)

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
                row=connection.execute("SELECT COALESCE(terminal_status,lifecycle_status,generation_status,status) FROM contracts WHERE id=?",(contract_id,)).fetchone()
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
                    "UPDATE contracts SET lifecycle_status=?,updated_at_utc=? WHERE id=? AND lifecycle_status IS NULL AND terminal_status IS NULL AND generation_status='TO_SIGN'",
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
        return self.reconcile_lifecycle(today)

    def reconcile_lifecycle(self,today:date|None=None)->tuple[str,...]:
        business_date=today or self.date_provider.today();failed=[]
        with self.database.connection() as connection:
            ids=[row[0] for row in connection.execute("SELECT id FROM contracts WHERE terminal_status IS NULL AND (lifecycle_status IN ('SIGNED','ACTIVE'))").fetchall()]
        for contract_id in ids:
            try:self._reconcile_contract(contract_id,business_date)
            except ContractLifecycleError:failed.append(contract_id)
        return tuple(failed)

    def _reconcile_contract(self,contract_id:str,today:date)->None:
        contract=self.contracts.get(contract_id);projection=self.lifecycle_projection(contract_id);pending=projection.pending_termination
        termination_date=date.fromisoformat(pending.effective_date) if pending and pending.effective_date else None
        if termination_date and termination_date<=today and termination_date<projection.authority.start_date:
            self._terminate_due(contract_id,termination_date);return
        if contract.status is ContractStatus.SIGNED and projection.authority.start_date<=today:
            self._activate_if_due(contract_id,today);contract=self.contracts.get(contract_id)
        if termination_date and termination_date<=today:
            self._terminate_due(contract_id,termination_date);return
        if contract.status is ContractStatus.ACTIVE and projection.period.end<=today:
            if projection.renewal_mode in {"NONE","MANUAL"} or (projection.renewal_mode=="TACIT" and projection.non_renewal_event):
                self._expire_due(contract_id,projection.period.end)

    def _terminate_due(self,contract_id:str,effective:date)->bool:
        now=self._ordered_now(contract_id);event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.TERMINATED,now,effective.isoformat())
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT terminal_status FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0] is not None:return False
                if not connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='TERMINATION_SCHEDULED' AND effective_date<=?",(contract_id,effective.isoformat())).fetchone():return False
                if self._event_exists(connection,contract_id,ContractEventType.TERMINATED):return False
                ContractEventRepository.insert(connection,event);connection.execute("UPDATE contracts SET terminal_status='TERMINATED',updated_at_utc=? WHERE id=?",(now,contract_id));return True
        except (ContractNotFoundError,ContractLifecycleError):raise
        except sqlite3.Error as exc:raise ContractLifecycleError("termination reconciliation") from exc

    def _expire_due(self,contract_id:str,effective:date)->bool:
        now=self._ordered_now(contract_id);event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.EXPIRED,now,effective.isoformat())
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT terminal_status,lifecycle_status FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[0] is not None:return False
                if row[1]!="ACTIVE":raise ContractLifecycleError("expiry state")
                if self._event_exists(connection,contract_id,ContractEventType.EXPIRED):return False
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='TERMINATION_SCHEDULED' AND effective_date<=?",(contract_id,effective.isoformat())).fetchone():return False
                ContractEventRepository.insert(connection,event);connection.execute("UPDATE contracts SET terminal_status='EXPIRED',updated_at_utc=? WHERE id=?",(now,contract_id));return True
        except (ContractNotFoundError,ContractLifecycleError):raise
        except sqlite3.Error as exc:raise ContractLifecycleError("expiry reconciliation") from exc

    def _activate_if_due(self,contract_id:str,today:date)->bool:
        authority=self.signature_authority(contract_id)
        if authority is None or authority.start_date>today:return False
        now=self.now_provider();event=ContractEvent(str(uuid.uuid4()),contract_id,ContractEventType.ACTIVATED,now,authority.start_date.isoformat())
        try:
            with self.database.transaction() as connection:
                row=connection.execute("SELECT lifecycle_status,terminal_status FROM contracts WHERE id=?",(contract_id,)).fetchone()
                if row is None:raise ContractNotFoundError(contract_id)
                if row[1] is not None:return False
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
    def _price(annual_ht,vat_rate,rate_is_percent:bool=False)->ContractPrice:
        ht=Decimal(str(annual_ht));rate=Decimal(str(vat_rate))
        if not rate_is_percent and rate<=1:rate*=Decimal("100")
        if ht<0 or rate<0:raise InvalidOperation
        vat=(ht*rate/Decimal("100")).quantize(Decimal("0.01"));ttc=(ht+vat).quantize(Decimal("0.01"))
        return ContractPrice(ht.quantize(Decimal("0.01")),rate,vat,ttc)

    @classmethod
    def _price_event(cls,event:ContractEvent)->ContractPrice:
        price=cls._price(event.renewal_annual_ht,event.renewal_vat_rate,rate_is_percent=True)
        if cls._decimal_text(price.vat_amount)!=cls._decimal_text(Decimal(event.renewal_vat_amount or "")) or cls._decimal_text(price.annual_ttc)!=cls._decimal_text(Decimal(event.renewal_annual_ttc or "")):raise InvalidOperation
        return price

    def _allowed_vat_rates(self,document:ContractDocument)->set[str]:
        try:return {format(Decimal(value),"f") for value in self.contracts.template_catalog.get_version(document.template_version_id).catalogs.vat_rates}
        except Exception as exc:raise ContractLifecycleError("vat catalog integrity","Les taux de TVA du contrat signé ne peuvent pas être déterminés.") from exc

    @staticmethod
    def _decimal_text(value:Decimal)->str:return format(value.quantize(Decimal("0.01")),"f")
    @staticmethod
    def _positive_int(value)->int|None:
        if value is None:return None
        parsed=int(value)
        if parsed<1:raise ValueError
        return parsed
    @staticmethod
    def _nonnegative_int(value)->int|None:
        if value is None:return None
        parsed=int(value)
        if parsed<0:raise ValueError
        return parsed

    @staticmethod
    def _client_snapshot(data:dict)->ClientSnapshot:
        return ClientSnapshot(**{name:str(data.get(name) or "") for name in ClientSnapshot.__dataclass_fields__})
    @staticmethod
    def _site_snapshot(data:dict)->SiteSnapshot:
        return SiteSnapshot(**{name:str(data.get(name) or "") for name in SiteSnapshot.__dataclass_fields__})
    @staticmethod
    def _equipment_snapshot(data:dict)->EquipmentSnapshot:
        return EquipmentSnapshot(equipment_type=str(data.get("type") or ""),brand=str(data.get("brand") or ""),model=str(data.get("model") or ""),
            serial_number=str(data.get("serial_number") or ""),power_kw=data.get("power_kw"),location=str(data.get("location") or ""),installation_date=str(data.get("install_date") or ""))

    @staticmethod
    def _assert_active_tacit(connection:sqlite3.Connection,contract_id:str,period:ContractPeriod)->None:
        row=connection.execute("SELECT terminal_status,lifecycle_status FROM contracts WHERE id=?",(contract_id,)).fetchone()
        if row is None or row[0] is not None or row[1]!="ACTIVE":raise ContractLifecycleError("renewal state")
        if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='RENEWAL_NOTICE_RECORDED' AND period_start=? AND period_end=?",(contract_id,period.start.isoformat(),period.end.isoformat())).fetchone():raise ContractLifecycleError("period already resolved")
        if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='RENEWAL_CONFIRMED' AND period_start=?",(contract_id,(period.end+timedelta(days=1)).isoformat())).fetchone():raise ContractLifecycleError("period already renewed")

    @staticmethod
    def _event_exists(connection:sqlite3.Connection,contract_id:str,event_type:ContractEventType)->bool:
        return connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type=?",(contract_id,event_type.value)).fetchone() is not None

    def _ordered_now(self,contract_id:str)->str:
        proposed=self.now_provider();events=self.events.list_for_contract(contract_id)
        latest=max((event.occurred_at for event in events),default="")
        return _after(latest) if latest>=proposed else proposed
