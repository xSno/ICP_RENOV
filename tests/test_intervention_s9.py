from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication,QDialogButtonBox,QLabel,QPushButton

from icp_renov_contracts.documents import ProductionDocxRenderer,StaticCompanyDocumentDataProvider,TemplateSourceStore
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.domain import ContractDocument,DocumentKind,TemplateVersionStatus
from icp_renov_contracts.repositories import ContractDocumentRepository,ContractEventRepository
from icp_renov_contracts.services import InterventionInput,InterventionSheetGenerationService
from icp_renov_contracts.ui.documents_view import DocumentsView,InterventionSheetDialog

from test_document_generation import COMPANY,FakeConverter,GenerationCase
from test_master_data import equipment,organization,site


class InterventionCase(GenerationCase):
    def setUp(self):
        super().setUp()
        self.sheet_template=self.catalog.create_template("Fiche synthétique", "INTERVENTION_SHEET")
        self.sheet_version=self.catalog.create_version(self.sheet_template,"SYNTH-S9",TemplateVersionStatus.AVAILABLE,())
        source=Path(__file__).parents[1]/"templates"/"ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx"
        rel,digest=self.store.import_source(source,self.sheet_version.id)
        self.sheet_version=self.catalog.set_generation_metadata(self.sheet_version.id,rel,digest,("phone","email"))
        self.sheet_service=InterventionSheetGenerationService(
            self.context.database,self.contracts,self.documents,self.store,ProductionDocxRenderer(),FakeConverter(),
            StaticCompanyDocumentDataProvider(COMPANY),self.context.workspace.root)

    def generate_sheet(self,technician="Camille",**changes):
        values={"date":"2026-08-14","technician":technician,"other":"Contrôle annuel","notes":"Ligne 1\nLigne 2",
                "issues":"Aucune anomalie","quote_recommended":False};values.update(changes)
        return self.sheet_service.generate(self.contract.id,self.sheet_version.id,InterventionInput(**values))


class MigrationAndCatalogTests(InterventionCase):
    def test_migration_nine_constraints_and_contract_rules(self):
        with self.context.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0],9)
            columns={row[1]:row for row in connection.execute("PRAGMA table_info(contract_documents)")}
            self.assertEqual(columns["revision_index"][3],0)
        with self.context.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO contract_documents(id,contract_id,document_kind,revision_index,generated_at_utc,template_version_id,docx_relpath,pdf_relpath,snapshot_json,docx_sha256,pdf_sha256) VALUES ('bad1',?,'CONTRACT',NULL,'now',?,'a','b','{}','x','y')",(self.contract.id,self.sheet_version.id))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO contract_documents(id,contract_id,document_kind,revision_index,generated_at_utc,template_version_id,docx_relpath,pdf_relpath,snapshot_json,docx_sha256,pdf_sha256) VALUES ('bad2',?,'INTERVENTION_SHEET',1,'now',?,'c','d','{}','x','y')",(self.contract.id,self.sheet_version.id))
        with self.assertRaises(ValueError):ContractDocument("x",self.contract.id,DocumentKind.CONTRACT,None,"now",self.sheet_version.id,"a","b","{}","x","y")
        with self.assertRaises(ValueError):ContractDocument("x",self.contract.id,DocumentKind.INTERVENTION_SHEET,1,"now",self.sheet_version.id,"a","b","{}","x","y")

    def test_sheet_catalog_is_kind_only_zero_regime_and_requiredness_closed(self):
        self.assertEqual(self.sheet_template.contract_type_code,"")
        self.assertEqual([item.id for item in self.catalog.list_available_intervention_sheets()],[self.sheet_version.id])
        self.assertNotIn(self.version.id,[item.id for item in self.catalog.list_available_intervention_sheets()])
        archived=self.catalog.create_version(self.sheet_template,"ARCH",TemplateVersionStatus.ARCHIVED,())
        pending=self.catalog.create_version(self.sheet_template,"PENDING",TemplateVersionStatus.TO_VALIDATE,())
        self.assertNotIn(archived.id,[item.id for item in self.catalog.list_available_intervention_sheets()]);self.assertNotIn(pending.id,[item.id for item in self.catalog.list_available_intervention_sheets()])
        with self.assertRaises(Exception):self.catalog.set_generation_metadata(self.sheet_version.id,"x","h",(),("intervention.unknown",))


class InterventionGenerationTests(InterventionCase):
    def test_success_has_no_number_revision_status_or_signed_semantics(self):
        before=self.contracts.get(self.contract.id);contract_documents=len(self.documents.list_for_contract_kind(self.contract.id,DocumentKind.CONTRACT))
        result=self.generate_sheet();after=self.contracts.get(self.contract.id);document=result.document
        self.assertIs(document.document_kind,DocumentKind.INTERVENTION_SHEET);self.assertIsNone(document.revision);self.assertIsNone(document.revision_index)
        self.assertEqual((after.number,after.status),(before.number,before.status));self.assertEqual(len(self.documents.list_for_contract_kind(self.contract.id,DocumentKind.CONTRACT)),contract_documents)
        self.assertIsNone(document.signed_pdf_path);self.assertTrue(document.docx_relpath.startswith(f"documents/contracts/{self.contract.id}/other/intervention/"))
        events=self.context.lifecycle.history(self.contract.id);self.assertEqual(sum(e.document_id==document.id for e in events),1)

    def test_all_fields_equipment_order_and_no_internal_note_leak(self):
        site_id=self.contracts.get(self.contract.id).site_source_id
        for index in range(1,10):
            created=self.master.create_equipment(site_id,equipment(f"Unité {index:02d}",f"Zone {index:02d}",internal_notes=f"SECRET-{index}"));self.contracts.select_equipment(self.contract.id,created.id)
        result=self.generate_sheet(technician="",quote_recommended=True);snapshot=result.document.snapshot
        self.assertEqual(snapshot["intervention"]["notes"],"Ligne 1\nLigne 2");self.assertEqual([item["position"] for item in snapshot["contract"]["equipment_items"]],list(range(10)))
        self.assertNotIn("SECRET",json.dumps(snapshot));text=self.docx_text(result.docx_path);self.assertIn("Oui",text);self.assertNotIn("True",text)

    def test_thirty_equipment_sheet_preserves_order_without_revision(self):
        site_id=self.contracts.get(self.contract.id).site_source_id
        for index in range(1,30):
            created=self.master.create_equipment(site_id,equipment(f"Unité {index:02d}",f"Zone {index:02d}",internal_notes="SECRET"));self.contracts.select_equipment(self.contract.id,created.id)
        result=self.generate_sheet();items=result.document.snapshot["contract"]["equipment_items"]
        self.assertEqual([item["position"] for item in items],list(range(30)));self.assertIsNone(result.document.revision)
        self.assertNotIn("SECRET",json.dumps(result.document.snapshot))

    def test_version_can_require_technician_without_global_requirement(self):
        self.generate_sheet(technician="")
        other=self.catalog.create_version(self.sheet_template,"TECH",TemplateVersionStatus.AVAILABLE,())
        source=Path(__file__).parents[1]/"templates"/"ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx";rel,digest=self.store.import_source(source,other.id)
        other=self.catalog.set_generation_metadata(other.id,rel,digest,(),("intervention.technician",))
        with self.assertRaises(DocumentGenerationError):self.sheet_service.generate(self.contract.id,other.id,InterventionInput("2026-08-14"))
        self.sheet_service.generate(self.contract.id,other.id,InterventionInput("2026-08-14","Camille"))

    def test_empty_optional_fields_remove_complete_source_lines(self):
        result=self.generate_sheet(technician="",other="",notes="",issues="",quote_recommended=None);text=self.docx_text(result.docx_path)
        self.assertEqual(text.count("Technicien"),1)  # signature heading remains; metadata row is removed
        for orphan in ("Autre :","Observations et anomalies","Observations :","Anomalies constatées :","Devis complémentaire recommandé"):
            self.assertNotIn(orphan,text)

    def test_multiple_sheets_are_distinct_and_template_freezes_after_use(self):
        items=[self.generate_sheet(),self.generate_sheet(),self.generate_sheet(date="2026-11-20")]
        self.assertEqual(len({item.document.id for item in items}),3);self.assertEqual(len({item.document.docx_relpath for item in items}),3)
        with self.assertRaises(Exception):self.catalog.set_generation_metadata(self.sheet_version.id,"other.docx","0"*64,())

    def test_atomic_database_or_event_failure_leaves_no_sheet_or_files(self):
        before=self.contracts.get(self.contract.id)
        with patch.object(ContractEventRepository,"insert",side_effect=sqlite3.OperationalError("event")):
            with self.assertRaises(DocumentGenerationError):self.generate_sheet()
        self.assertEqual(self.documents.list_for_contract_kind(self.contract.id,DocumentKind.INTERVENTION_SHEET),())
        self.assertEqual((self.contracts.get(self.contract.id).number,self.contracts.get(self.contract.id).status),(before.number,before.status))
        root=self.context.workspace.root/"documents"/"contracts"/self.contract.id/"other"/"intervention";self.assertFalse(any(root.rglob("*.pdf")) if root.exists() else False)

    def test_signed_r01_is_authority_over_unsigned_r02_and_live_masters(self):
        r01=self.service().generate(self.contract.id);self.context.lifecycle.reopen_for_correction(self.contract.id)
        self.contracts.update_signatory(self.contract.id,"R02 différent","R02");r02=self.service().generate(self.contract.id)
        self.context.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13")
        contract=self.contracts.get(self.contract.id);self.master.update_client(contract.client_source_id,organization("MAÎTRE MODIFIÉ"));self.master.update_site(contract.site_source_id,site("SITE MODIFIÉ"))
        sheet=self.generate_sheet().document.snapshot
        self.assertEqual(sheet["authority"]["signed_contract_document_id"],r01.document.id)
        self.assertEqual(sheet["client"],r01.document.snapshot["client"]);self.assertNotEqual(sheet["client"],r02.document.snapshot["client"] if r02.document.snapshot["client"]!=r01.document.snapshot["client"] else {"different":True})
        self.assertNotIn("MAÎTRE MODIFIÉ",json.dumps(sheet,ensure_ascii=False));self.assertNotIn("SITE MODIFIÉ",json.dumps(sheet,ensure_ascii=False))

    def test_contract_only_events_reject_sheet(self):
        sheet=self.generate_sheet().document
        with self.assertRaises(Exception):self.context.lifecycle.record_send(self.contract.id,sheet.id,"2026-08-14")
        with self.assertRaises(Exception):self.context.lifecycle.record_signature(self.contract.id,sheet.id,"2026-08-14")


class InterventionUiTests(InterventionCase):
    @classmethod
    def setUpClass(cls):cls.application=QApplication.instance() or QApplication([])
    def test_modal_fields_no_model_and_other_documents_card(self):
        dialog=InterventionSheetDialog([]);self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertIn("Aucun modèle",dialog.empty.text());dialog.close()
        messages=[];view=DocumentsView(self.context.lifecycle,lambda message,error:messages.append((message,error)),interventions=self.sheet_service)
        view.load(self.contract.id);self.assertIsNotNone(view.findChild(QPushButton,"createInterventionSheet"))
        self.generate_sheet();view.load(self.contract.id);labels=" ".join(item.text() for item in view.findChildren(QLabel))
        self.assertIn("Autres documents",labels);self.assertIn("Fiche d’intervention",labels);self.assertNotIn("R00",labels)
        view.close();dialog.deleteLater();view.deleteLater();self.application.processEvents()


if __name__=="__main__":unittest.main()
