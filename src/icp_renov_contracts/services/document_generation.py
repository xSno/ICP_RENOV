from __future__ import annotations

from dataclasses import asdict,dataclass
from datetime import datetime,timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import uuid

from ..database import DatabaseService
from ..documents import CompanyDocumentDataProvider,ContractNumberAllocator,PdfConverter,ProductionDocxRenderer,TemplateSourceStore
from ..documents.source_store import sha256_file
from ..documents.formatters import prepare_context
from ..documents.validation import DocumentGenerationError,validate_pdf,validate_rendered_docx
from ..domain import ContractDocument,ContractEvent,ContractEventType,ContractStatus,DocumentKind,TemplateVersionStatus
from ..repositories import ContractDocumentRepository,ContractEventRepository
from .contracts import ContractService
from .review import ReviewService

@dataclass(frozen=True)
class GenerationResult:
    document:ContractDocument
    contract_number:str
    docx_path:Path
    pdf_path:Path

class DocumentGenerationService:
    def __init__(self,database:DatabaseService,contracts:ContractService,documents:ContractDocumentRepository,
                 review:ReviewService,source_store:TemplateSourceStore,renderer:ProductionDocxRenderer,
                 converter:PdfConverter,company_provider:CompanyDocumentDataProvider,number_allocator:ContractNumberAllocator,
                 workspace_root:Path,logger:logging.Logger|None=None)->None:
        self.database=database;self.contracts=contracts;self.documents=documents;self.review_service=review;self.source_store=source_store
        self.renderer=renderer;self.converter=converter;self.company_provider=company_provider;self.number_allocator=number_allocator
        self.workspace_root=workspace_root.resolve();self.logger=logger or logging.getLogger("icp_renov_contracts.generation")
    def available(self,contract_id:str)->bool:
        try:
            contract=self.contracts.get(contract_id);version=self.contracts.selected_template_version(contract_id);documents=self.documents.list_for_contract(contract_id)
            if contract.status is not ContractStatus.DRAFT or not version or not self.company_provider.available() or not self.converter.available():return False
            with self.database.connection() as connection:
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='SIGNATURE_RECORDED'",(contract_id,)).fetchone():return False
            if bool(contract.number)!=bool(documents):return False
            if not documents and not self.number_allocator.available():return False
            company=self._prepared_company(self.company_provider.get());return all(company.get(key) not in (None,"") for key in version.required_company_fields)
        except Exception:return False
    def preview_number(self,contract_id:str|None=None)->str|None:
        if contract_id:
            contract=self.contracts.get(contract_id)
            if contract.number:return contract.number
        return self.number_allocator.preview_next() if self.number_allocator.available() else None
    def next_revision(self,contract_id:str)->str:
        documents=self.documents.list_for_contract(contract_id)
        indices=[item.revision_index for item in documents]
        if len(indices)!=len(set(indices)):raise DocumentGenerationError("revision_integrity","L’historique des révisions est incohérent.")
        return f"R{(max(indices,default=0)+1):02d}"
    def generate(self,contract_id:str)->GenerationResult:
        attempt=uuid.uuid4().hex;stage="review";attempt_dir=self.workspace_root/"tmp"/"generation"/attempt
        final_docx=None;final_pdf=None
        try:
            contract=self.contracts.get(contract_id);existing=self.documents.list_for_contract(contract_id)
            if contract.status is not ContractStatus.DRAFT:
                raise DocumentGenerationError("status","Ce contrat ne peut plus être généré.")
            with self.database.connection() as connection:
                if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='SIGNATURE_RECORDED'",(contract_id,)).fetchone():
                    raise DocumentGenerationError("signed","Ce contrat signé ne peut plus produire de nouvelle révision.")
            if bool(contract.number)!=bool(existing):raise DocumentGenerationError("revision_integrity","L’historique des révisions est incohérent.")
            indices=[item.revision_index for item in existing]
            if len(indices)!=len(set(indices)):raise DocumentGenerationError("revision_integrity","L’historique des révisions est incohérent.")
            revision_index=max(indices,default=0)+1;revision=f"R{revision_index:02d}";first=not existing
            review=self.review_service.review(contract_id)
            if not review.data_complete:raise DocumentGenerationError("incomplete","Le contrat doit être complet avant la génération.")
            stage="template_preflight";version=self.contracts.selected_template_version(contract_id)
            if not version or version.document_kind!="CONTRACT" or version.status is not TemplateVersionStatus.AVAILABLE or version.contract_type_code!=contract.type_code.value or not contract.regime or contract.regime.value not in version.allowed_client_regimes:
                raise DocumentGenerationError("template_incompatible","Le modèle sélectionné n’est pas disponible pour ce contrat.")
            try:source=self.source_store.verify(version.source_relpath,version.source_hash)
            except (FileNotFoundError,ValueError) as exc:raise DocumentGenerationError("source_mismatch","Le modèle sélectionné ne correspond plus à sa version validée.") from exc
            stage="company_preflight"
            if not self.company_provider.available():raise DocumentGenerationError("company_unavailable","Les informations de l’entreprise nécessaires à la génération ne sont pas configurées.")
            company=self.company_provider.get();prepared_company=self._prepared_company(company);missing=[key for key in version.required_company_fields if prepared_company.get(key) in (None,"")]
            if missing:raise DocumentGenerationError("company_incomplete","Les informations de l’entreprise nécessaires à la génération sont incomplètes.")
            if not self.converter.available():raise DocumentGenerationError("converter_unavailable","Le PDF n’a pas pu être créé.")
            stage="number_preview"
            if first:
                if not self.number_allocator.available() or not (preview:=self.number_allocator.preview_next()):raise DocumentGenerationError("numbering_unavailable","La numérotation des contrats n’est pas configurée.")
            else:preview=contract.number
            stage="workspace_write";attempt_dir.mkdir(parents=True,exist_ok=False);probe=attempt_dir/".write-test";probe.write_bytes(b"ok");probe.unlink()
            stage="snapshot";snapshot=self._snapshot(contract,self.contracts.get_conditions(contract_id),version,company,preview,revision)
            snapshot_json=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            docx=attempt_dir/"contract.docx";pdf=attempt_dir/"contract.pdf"
            stage="render";self.renderer.render(source,docx,snapshot,attempt_dir)
            stage="docx_postflight";validate_rendered_docx(docx)
            stage="conversion";self.converter.convert(docx,pdf)
            stage="pdf_postflight";validate_pdf(pdf)
            docx_hash=sha256_file(docx);pdf_hash=sha256_file(pdf);generated=datetime.now(timezone.utc).isoformat();document_id=str(uuid.uuid4())
            rel_dir=Path("documents")/"contracts"/contract.id/revision;docx_rel=(rel_dir/f"{preview}_{revision}.docx").as_posix();pdf_rel=(rel_dir/f"{preview}_{revision}.pdf").as_posix()
            final_docx=(self.workspace_root/Path(docx_rel)).resolve();final_pdf=(self.workspace_root/Path(pdf_rel)).resolve()
            if final_docx.exists() or final_pdf.exists():raise DocumentGenerationError("collision","La génération n’a pas pu être finalisée. Aucun numéro ni aucune révision n’a été créé.")
            document=ContractDocument(document_id,contract.id,DocumentKind.CONTRACT,revision_index,generated,version.id,docx_rel,pdf_rel,snapshot_json,docx_hash,pdf_hash)
            generated_event=ContractEvent(str(uuid.uuid4()),contract.id,ContractEventType.DOCUMENT_GENERATED,generated,document_id=document_id)
            stage="publication";moved=[]
            try:
                with self.database.transaction() as connection:
                    current=connection.execute("SELECT COALESCE(generation_status,status),number FROM contracts WHERE id=?",(contract.id,)).fetchone()
                    if connection.execute("SELECT 1 FROM contract_events WHERE contract_id=? AND type='SIGNATURE_RECORDED'",(contract.id,)).fetchone():
                        raise DocumentGenerationError("signed","Ce contrat signé ne peut plus produire de nouvelle révision.")
                    durable=connection.execute("SELECT revision_index FROM contract_documents WHERE contract_id=? AND document_kind='CONTRACT' ORDER BY revision_index",(contract.id,)).fetchall()
                    if not current or current[0]!="DRAFT" or current[1]!=contract.number or [row[0] for row in durable]!=indices:raise DocumentGenerationError("status","Ce contrat ne peut plus être généré.")
                    allocated=self.number_allocator.allocate(connection) if first else current[1]
                    if allocated!=preview:raise DocumentGenerationError("number_changed","La numérotation a changé. Relancez la génération.")
                    final_docx.parent.mkdir(parents=True,exist_ok=True);docx.replace(final_docx);moved.append(final_docx);pdf.replace(final_pdf);moved.append(final_pdf)
                    ContractDocumentRepository.insert(connection,document)
                    ContractEventRepository.insert(connection,generated_event)
                    cursor=connection.execute("UPDATE contracts SET number=?,generation_status='TO_SIGN',updated_at_utc=? WHERE id=? AND status='DRAFT' AND generation_status IS NULL AND number IS ?",(allocated,generated,contract.id,contract.number))
                    if cursor.rowcount!=1:raise sqlite3.IntegrityError("contract publication state changed")
            except Exception:
                for path in reversed(moved):
                    try:path.unlink()
                    except OSError:pass
                raise
            return GenerationResult(document,preview,final_docx,final_pdf)
        except DocumentGenerationError:raise
        except (OSError,sqlite3.Error) as exc:
            message=("Espace disponible ou écriture insuffisante pour terminer la génération." if stage=="workspace_write" else "La génération n’a pas pu être finalisée. Aucun numéro ni aucune révision n’a été créé.")
            raise DocumentGenerationError("publication_failure",message) from exc
        except Exception as exc:
            raise DocumentGenerationError("generation_failure","La génération n’a pas pu être finalisée. Aucun numéro ni aucune révision n’a été créé.") from exc
        finally:
            self.logger.info("document generation attempt=%s stage=%s contract=%s",attempt,stage,contract_id)
            if attempt_dir.exists():shutil.rmtree(attempt_dir,ignore_errors=True)
    @staticmethod
    def _prepared_company(company):
        return prepare_context({"company":company,"client":{},"site":{},"contract":{"equipment_items":[]},"service":{},"pricing":{}})["company"]
    @staticmethod
    def _snapshot(contract,conditions,version,company,number,revision="R01"):
        client=asdict(contract.client_snapshot);client.update({"regime":contract.regime.value,"representative_name":contract.signatory_name,"representative_role":contract.signatory_role,"signatory_name":contract.signatory_name,"signatory_role":contract.signatory_role,"postal_address":client.get("address_line1","")})
        site=asdict(contract.site_snapshot);items=[]
        for item in contract.equipment_items:
            equipment=asdict(item.snapshot);items.append({"position":item.position,"type":equipment["equipment_type"],"brand":equipment["brand"],"model":equipment["model"],"serial_number":equipment["serial_number"],"power_kw":equipment["power_kw"],"location":equipment["location"],"install_date":equipment["installation_date"],"observations":item.observation})
        selected=tuple(sorted(version.validation.blocks_for(contract.regime.value,conditions.conclusion_mode)))
        contract_data={"number":number,"type_code":contract.type_code.value,"conclusion_mode":conditions.conclusion_mode,"early_performance_requested":conditions.early_performance_requested,
                       "visits_per_year":conditions.visits_per_year,"breach_cure_period_days":conditions.breach_cure_period_days,
                       "issue_date":conditions.issue_date,"start_date":conditions.start_date,"initial_duration_mode":conditions.initial_duration_mode,"initial_duration_months":conditions.initial_duration_months,
                       "initial_end_date":conditions.resolved_end_date,"signature_city":conditions.signature_city,"renewal_mode":conditions.renewal_mode,"renewal_period_months":conditions.renewal_period_months,
                       "non_renewal_notice_days":conditions.non_renewal_notice_days,"non_renewal_notice_channels":list(conditions.non_renewal_notice_channels),"internal_alert_days":conditions.internal_alert_days,
                       "special_terms":conditions.special_terms,"equipment_items":items}
        service={key:getattr(conditions,key) for key in ("visits_per_year","refrigerant_handling_mode","included_area","business_hours","travel_included","priority_breakdown","priority_breakdown_delay","included_options","additional_exclusions")};service["included_options"]=list(service["included_options"])
        pricing={"annual_ht":conditions.annual_ht,"vat_rate":str((Decimal(conditions.vat_rate or '0')/100)),"payment_terms_code":conditions.payment_terms_code,"payment_due_days":conditions.payment_due_days,
                 "payment_terms_custom_text":conditions.payment_terms_custom_text,"payment_methods":list(conditions.payment_methods),"missed_appointment_fee":conditions.missed_appointment_fee,"renewal_price_rule":conditions.renewal_price_rule}
        return {"company":company,"client":client,"site":site,"contract":contract_data,"service":service,"pricing":pricing,"document":{"revision":revision},
                "template":{"id":version.template_id,"version_id":version.id,"version":version.version,"source_hash":version.source_hash,"source_relpath":version.source_relpath,
                            "selected_blocks":list(selected),"required_company_fields":list(version.required_company_fields)}}
