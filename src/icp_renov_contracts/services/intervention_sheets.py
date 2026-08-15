from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict,dataclass
from datetime import date,datetime,timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import uuid

from ..database import DatabaseService
from ..documents import CompanyDocumentDataProvider,PdfConverter,ProductionDocxRenderer,TemplateSourceStore
from ..documents.formatters import prepare_context
from ..documents.source_store import sha256_file
from ..documents.validation import DocumentGenerationError,validate_pdf,validate_rendered_docx
from ..domain import ContractDocument,ContractEvent,ContractEventType,DocumentKind,TemplateVersionStatus
from ..repositories import ContractDocumentRepository,ContractEventRepository
from .contracts import ContractService


@dataclass(frozen=True)
class InterventionInput:
    date: str
    technician: str = ""
    other: str = ""
    notes: str = ""
    issues: str = ""
    quote_recommended: bool | None = None


@dataclass(frozen=True)
class InterventionGenerationResult:
    document: ContractDocument
    docx_path: Path
    pdf_path: Path


class InterventionSheetGenerationService:
    """Atomic, non-lifecycle publication of immutable intervention sheets."""
    def __init__(self,database:DatabaseService,contracts:ContractService,documents:ContractDocumentRepository,
                 source_store:TemplateSourceStore,renderer:ProductionDocxRenderer,converter:PdfConverter,
                 company_provider:CompanyDocumentDataProvider,workspace_root:Path,
                 logger:logging.Logger|None=None)->None:
        self.database=database;self.contracts=contracts;self.documents=documents;self.source_store=source_store
        self.renderer=renderer;self.converter=converter;self.company_provider=company_provider
        self.workspace_root=workspace_root.resolve();self.logger=logger or logging.getLogger("icp_renov_contracts.intervention")

    def available_templates(self):
        catalog=self.contracts.template_catalog
        return catalog.list_available_intervention_sheets() if catalog else []

    def available(self,contract_id:str)->bool:
        try:
            contract=self.contracts.get(contract_id)
            return bool(contract.client_snapshot and contract.site_snapshot and contract.equipment_items
                        and self.available_templates() and self.company_provider.available() and self.converter.available())
        except Exception:return False

    def generate(self,contract_id:str,template_version_id:str,values:InterventionInput)->InterventionGenerationResult:
        attempt=uuid.uuid4().hex;attempt_dir=self.workspace_root/"tmp"/"generation"/attempt
        stage="input";final_docx=None;final_pdf=None;moved=[]
        try:
            try:date.fromisoformat(values.date)
            except (TypeError,ValueError) as exc:
                raise DocumentGenerationError("intervention_required","Complétez les informations nécessaires à la fiche d’intervention.") from exc
            if not isinstance(values.quote_recommended,(bool,type(None))):
                raise DocumentGenerationError("intervention_value","Complétez les informations nécessaires à la fiche d’intervention.")
            version=self.contracts.template_catalog.get_version(template_version_id) if self.contracts.template_catalog else None
            if (not version or version.document_kind!="INTERVENTION_SHEET"
                    or version.status is not TemplateVersionStatus.AVAILABLE):
                raise DocumentGenerationError("template_unavailable","Aucun modèle de fiche d’intervention disponible.")
            cleaned=InterventionInput(values.date,values.technician.strip(),values.other.strip(),values.notes,values.issues,values.quote_recommended)
            if any(getattr(cleaned,key.split(".",1)[1])=="" for key in version.required_intervention_fields):
                raise DocumentGenerationError("intervention_required","Complétez les informations nécessaires à la fiche d’intervention.")
            stage="source_preflight"
            try:source=self.source_store.verify(version.source_relpath,version.source_hash)
            except (FileNotFoundError,ValueError) as exc:
                raise DocumentGenerationError("source_mismatch","Le modèle de fiche d’intervention ne correspond plus à sa version validée.") from exc
            if not self.company_provider.available():
                raise DocumentGenerationError("company_unavailable","Complétez les informations nécessaires à la fiche d’intervention.")
            company=self.company_provider.get();prepared=self._prepared_company(company)
            if any(prepared.get(key) in (None,"") for key in version.required_company_fields):
                raise DocumentGenerationError("company_incomplete","Complétez les informations nécessaires à la fiche d’intervention.")
            if not self.converter.available():
                raise DocumentGenerationError("converter_unavailable","Le PDF de la fiche d’intervention n’a pas pu être créé.")
            contract=self.contracts.get(contract_id);authority=self._authority(contract_id,contract)
            attempt_dir.mkdir(parents=True,exist_ok=False);snapshot=self._snapshot(contract,authority,version,company,cleaned)
            snapshot_json=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            docx=attempt_dir/"intervention.docx";pdf=attempt_dir/"intervention.pdf"
            stage="render";self.renderer.render(source,docx,snapshot,attempt_dir)
            stage="docx_postflight";validate_rendered_docx(docx)
            stage="conversion";self.converter.convert(docx,pdf)
            stage="pdf_postflight";validate_pdf(pdf)
            generated=datetime.now(timezone.utc).isoformat();document_id=str(uuid.uuid4())
            rel_dir=Path("documents")/"contracts"/contract.id/"other"/"intervention"/document_id
            docx_rel=(rel_dir/"fiche-intervention.docx").as_posix();pdf_rel=(rel_dir/"fiche-intervention.pdf").as_posix()
            final_docx=(self.workspace_root/docx_rel).resolve();final_pdf=(self.workspace_root/pdf_rel).resolve()
            if final_docx.exists() or final_pdf.exists():raise DocumentGenerationError("collision","La fiche d’intervention n’a pas pu être finalisée. Aucun document officiel n’a été ajouté.")
            document=ContractDocument(document_id,contract.id,DocumentKind.INTERVENTION_SHEET,None,generated,version.id,
                                      docx_rel,pdf_rel,snapshot_json,sha256_file(docx),sha256_file(pdf))
            event=ContractEvent(str(uuid.uuid4()),contract.id,ContractEventType.DOCUMENT_GENERATED,generated,document_id=document_id)
            stage="publication"
            try:
                with self.database.transaction() as connection:
                    current=connection.execute(
                        "SELECT number,COALESCE(terminal_status,lifecycle_status,generation_status,status),updated_at_utc FROM contracts WHERE id=?",
                        (contract.id,),).fetchone()
                    if not current:raise DocumentGenerationError("contract_missing","La fiche d’intervention n’a pas pu être finalisée. Aucun document officiel n’a été ajouté.")
                    final_docx.parent.mkdir(parents=True,exist_ok=True);docx.replace(final_docx);moved.append(final_docx);pdf.replace(final_pdf);moved.append(final_pdf)
                    ContractDocumentRepository.insert(connection,document);ContractEventRepository.insert(connection,event)
            except Exception:
                for path in reversed(moved):
                    try:path.unlink()
                    except OSError:pass
                raise
            return InterventionGenerationResult(document,final_docx,final_pdf)
        except DocumentGenerationError:raise
        except (OSError,sqlite3.Error) as exc:
            raise DocumentGenerationError("publication_failure","La fiche d’intervention n’a pas pu être finalisée. Aucun document officiel n’a été ajouté.") from exc
        except Exception as exc:
            message="La fiche DOCX n’a pas pu être générée." if stage in {"render","docx_postflight"} else "Le PDF de la fiche d’intervention n’a pas pu être créé." if stage in {"conversion","pdf_postflight"} else "La fiche d’intervention n’a pas pu être finalisée. Aucun document officiel n’a été ajouté."
            raise DocumentGenerationError("generation_failure",message) from exc
        finally:
            self.logger.info("intervention generation attempt=%s stage=%s contract=%s",attempt,stage,contract_id)
            if attempt_dir.exists():shutil.rmtree(attempt_dir,ignore_errors=True)

    def _authority(self,contract_id,contract):
        with self.database.connection() as connection:
            row=connection.execute("SELECT document_id FROM contract_events WHERE contract_id=? AND type='SIGNATURE_RECORDED'",(contract_id,)).fetchone()
        if row:
            document=self.documents.get(row[0])
            if not document or document.document_kind is not DocumentKind.CONTRACT:raise DocumentGenerationError("authority","Le contexte contractuel ne peut pas être déterminé.")
            return {"kind":"SIGNED_CONTRACT_DOCUMENT","document_id":document.id,"snapshot":document.snapshot}
        return {"kind":"CONTRACT_OWNED_SNAPSHOT","document_id":None,"snapshot":None}

    @staticmethod
    def _prepared_company(company):
        return prepare_context({"company":company,"client":{},"site":{},"contract":{"equipment_items":[]},"service":{},"pricing":{},"intervention":{}})["company"]

    @staticmethod
    def _snapshot(contract,authority,version,company,values):
        if authority["snapshot"] is not None:
            signed=authority["snapshot"];client=deepcopy(signed.get("client",{}));site=deepcopy(signed.get("site",{}))
            signed_contract=signed.get("contract",{});number=signed_contract.get("number");items=deepcopy(signed_contract.get("equipment_items",[]))
        else:
            if contract.client_snapshot is None or contract.site_snapshot is None:
                raise DocumentGenerationError("context_incomplete","Complétez les informations nécessaires à la fiche d’intervention.")
            client=asdict(contract.client_snapshot);site=asdict(contract.site_snapshot);number=contract.number;items=[]
            for item in contract.equipment_items:
                equipment=asdict(item.snapshot)
                equipment.pop("internal_notes",None)
                items.append({"position":item.position,"type":equipment.get("equipment_type",""),"brand":equipment.get("brand",""),
                              "model":equipment.get("model",""),"serial_number":equipment.get("serial_number",""),
                              "power_kw":equipment.get("power_kw"),"location":equipment.get("location",""),
                              "install_date":equipment.get("installation_date"),"observations":item.observation})
        for item in items:item.pop("internal_notes",None)
        intervention=asdict(values)
        return {"document":{"document_kind":"INTERVENTION_SHEET"},"contract_internal_reference":contract.id,
                "company":deepcopy(company),"client":client,"site":site,"contract":{"number":number,"equipment_items":items},
                "intervention":intervention,
                "template":{"id":version.template_id,"version_id":version.id,"version":version.version,
                            "source_relpath":version.source_relpath,"source_hash":version.source_hash,
                            "required_company_fields":list(version.required_company_fields),
                            "required_intervention_fields":list(version.required_intervention_fields),"selected_blocks":[]},
                "authority":{"kind":authority["kind"],"signed_contract_document_id":authority["document_id"]}}
