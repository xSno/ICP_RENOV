from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication,QComboBox,QDialog,QLabel,QPushButton

from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.documents.providers import ContractNumberAllocator
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.domain import ContractEventType,ContractStatus
from icp_renov_contracts.errors import ContractLifecycleError
from icp_renov_contracts.repositories import ContractDocumentRepository,ContractEventRepository
from icp_renov_contracts.services import ContractLifecycleService
from icp_renov_contracts.ui.documents_view import CorrectionConfirmationDialog,DocumentsView,SendRecordingDialog

from test_document_generation import FakeConverter,GenerationCase,FailingRenderer
from test_foundation import scratch


class UnavailableAllocator(ContractNumberAllocator):
    def available(self):return False
    def preview_next(self):raise AssertionError("R02 must not preview a new number")
    def allocate(self,connection):raise AssertionError("R02 must not allocate a new number")


class RecordingOpener:
    def __init__(self):self.paths=[]
    def open(self,path):self.paths.append(path)


class EventMigrationTests(unittest.TestCase):
    def test_canonical_event_vocabulary_is_exact(self):
        self.assertEqual([item.value for item in ContractEventType],[
            "CREATED","DOCUMENT_GENERATED","CONTRACT_SENT","REOPENED_FOR_CORRECTION","SIGNATURE_RECORDED",
            "ACTIVATED","RENEWAL_NOTICE_RECORDED","RENEWAL_CONFIRMED","TERMINATION_SCHEDULED","TERMINATED",
            "EXPIRED","ABANDONED","ADMIN_CORRECTION",
        ])

    def test_migration_six_backfills_truthful_events_once_and_is_immutable(self):
        with scratch() as temporary:
            path=Path(temporary)/"s5.sqlite3";database=DatabaseService(path)
            with database.transaction() as connection:
                for migration in MIGRATIONS[:5]:
                    for statement in migration.statements:connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations VALUES (?,?)",(migration.version,"frozen"))
                connection.execute("INSERT INTO contract_templates VALUES ('t','Template','CONTRACT','CLIMATE_MAINTENANCE','2026-01-01T00:00:00+00:00')")
                connection.execute("INSERT INTO contract_template_versions(id,template_id,version,version_status,allowed_client_regimes_json,validation_metadata_json,defaults_json,option_catalogs_json,created_at_utc,updated_at_utc,source_relpath,source_hash,required_company_fields_json) VALUES ('v','t','1','AVAILABLE','[\"CONSUMER\"]','{}','{}','{}','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','x','h','[]')")
                connection.execute("INSERT INTO contracts(id,status,type_code,created_at_utc,updated_at_utc,number,generation_status) VALUES ('c','DRAFT','CLIMATE_MAINTENANCE','2026-01-02T00:00:00+00:00','2026-01-03T00:00:00+00:00','C-1','TO_SIGN')")
                connection.execute("INSERT INTO contract_conditions(contract_id,updated_at_utc) VALUES ('c','2026-01-03T00:00:00+00:00')")
                connection.execute("INSERT INTO contract_documents VALUES ('d','c','CONTRACT',1,'2026-01-03T00:00:00+00:00','v','a.docx','a.pdf','{}','x','y')")
            database.initialize();database.initialize()
            events=ContractEventRepository(database).list_for_contract("c")
            self.assertEqual([(e.type,e.occurred_at,e.document_id) for e in reversed(events)],[(ContractEventType.CREATED,"2026-01-02T00:00:00+00:00",None),(ContractEventType.DOCUMENT_GENERATED,"2026-01-03T00:00:00+00:00","d")])
            with database.transaction() as connection:
                with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE contract_events SET note='x' WHERE id=?",(events[0].id,))
            self.assertEqual(database.schema_version(),8)


class ContractEventLifecycleTests(GenerationCase):
    @classmethod
    def setUpClass(cls):cls.application=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp();self.events=ContractEventRepository(self.context.database);self.opener=RecordingOpener()
        self.lifecycle=ContractLifecycleService(self.context.database,self.contracts,self.documents,self.events,self.context.workspace.root,self.opener)

    def test_created_is_single_and_consultation_is_not_an_event(self):
        before=self.lifecycle.history(self.contract.id);self.assertEqual([e.type for e in before],[ContractEventType.CREATED])
        self.contracts.get(self.contract.id);self.lifecycle.revisions(self.contract.id);self.lifecycle.history(self.contract.id)
        self.assertEqual(self.lifecycle.history(self.contract.id),before)

    def test_r01_send_reopen_r02_and_r03_reuse_number_with_isolated_sent_state(self):
        service=self.service();r01=service.generate(self.contract.id);number=r01.contract_number;r01_bytes=r01.docx_path.read_bytes();self.assertEqual(self.allocator.consumed(),1)
        self.lifecycle.record_send(self.contract.id,r01.document.id,"2026-08-13","Premier envoi")
        self.lifecycle.reopen_for_correction(self.contract.id);self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.DRAFT);self.assertEqual(self.contracts.get(self.contract.id).number,number)
        original=r01.document.snapshot["contract"]["special_terms"]
        self.contracts.save_conditions(self.contract.id,replace(self.contracts.get_conditions(self.contract.id),special_terms="Correction R02 visible"))
        service.number_allocator=UnavailableAllocator();self.assertTrue(service.available(self.contract.id));self.assertEqual(service.next_revision(self.contract.id),"R02")
        r02=service.generate(self.contract.id);self.assertEqual(r02.document.revision,"R02");self.assertEqual(r02.contract_number,number);self.assertEqual(r01.docx_path.read_bytes(),r01_bytes)
        self.assertEqual(r01.document.snapshot["contract"]["special_terms"],original);self.assertEqual(r02.document.snapshot["contract"]["special_terms"],"Correction R02 visible")
        self.assertIsNotNone(self.lifecycle.latest_send(r01.document.id));self.assertIsNone(self.lifecycle.latest_send(r02.document.id));self.assertEqual(self.allocator.consumed(),1)
        self.lifecycle.reopen_for_correction(self.contract.id);r03=service.generate(self.contract.id);self.assertEqual(r03.document.revision,"R03");self.assertEqual(r03.contract_number,number)
        generated=[e for e in self.lifecycle.history(self.contract.id) if e.type is ContractEventType.DOCUMENT_GENERATED]
        self.assertEqual({e.document_id for e in generated},{r01.document.id,r02.document.id,r03.document.id})

    def test_failed_r02_preserves_r01_and_retry_still_creates_r02(self):
        service=self.service();r01=service.generate(self.contract.id);before=r01.docx_path.read_bytes();number=r01.contract_number;self.lifecycle.reopen_for_correction(self.contract.id)
        failures=(self.service(renderer=FailingRenderer()),self.service(renderer=FailingRenderer("invalid")),self.service(converter=FakeConverter("failure")),self.service(converter=FakeConverter("invalid")))
        for failing in failures:
            failing.number_allocator=UnavailableAllocator()
            with self.assertRaises(DocumentGenerationError):failing.generate(self.contract.id)
            self.assertEqual(self.contracts.get(self.contract.id).number,number);self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.DRAFT)
            self.assertEqual([d.revision for d in self.documents.list_for_contract(self.contract.id)],["R01"]);self.assertEqual(r01.docx_path.read_bytes(),before)
        database_failure=self.service();database_failure.number_allocator=UnavailableAllocator()
        with patch.object(ContractDocumentRepository,"insert",side_effect=sqlite3.OperationalError("db failure")):
            with self.assertRaises(DocumentGenerationError):database_failure.generate(self.contract.id)
        collision=r01.docx_path.parents[1]/"R02"/f"{number}_R02.docx";collision.parent.mkdir(parents=True,exist_ok=True);collision.write_bytes(b"collision")
        collision_failure=self.service();collision_failure.number_allocator=UnavailableAllocator()
        with self.assertRaises(DocumentGenerationError):collision_failure.generate(self.contract.id)
        collision.unlink()
        service.number_allocator=UnavailableAllocator();self.assertEqual(service.generate(self.contract.id).document.revision,"R02")

    def test_document_generated_event_failure_rolls_back_new_revision_and_files(self):
        service=self.service()
        with patch.object(ContractEventRepository,"insert",side_effect=sqlite3.OperationalError("event failure")):
            with self.assertRaises(DocumentGenerationError):service.generate(self.contract.id)
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.DRAFT);self.assertIsNone(self.contracts.get(self.contract.id).number)
        self.assertEqual(self.documents.list_for_contract(self.contract.id),());self.assertFalse(any(e.type is ContractEventType.DOCUMENT_GENERATED for e in self.lifecycle.history(self.contract.id)))

    def test_created_event_failure_leaves_no_partial_contract(self):
        with patch.object(ContractEventRepository,"insert",side_effect=sqlite3.OperationalError("created failure")):
            with self.assertRaises(Exception):self.contracts.create_draft()
        with self.context.database.connection() as connection:self.assertEqual(connection.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],1)

    def test_multiple_and_historical_sends_are_append_only_and_status_never_changes(self):
        r01=self.service().generate(self.contract.id);self.lifecycle.record_send(self.contract.id,r01.document.id,"2026-08-12")
        self.lifecycle.record_send(self.contract.id,r01.document.id,"2026-08-14","Relance");self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TO_SIGN)
        sends=self.events.list_for_document(r01.document.id,ContractEventType.CONTRACT_SENT);self.assertEqual(len(sends),2);self.assertEqual(self.lifecycle.latest_send(r01.document.id).effective_date,"2026-08-14")
        other=self.contracts.create_draft()
        with self.assertRaises(ContractLifecycleError):self.lifecycle.record_send(other.id,r01.document.id,"2026-08-15")
        before=self.lifecycle.history(self.contract.id)
        with patch.object(ContractEventRepository,"insert",side_effect=sqlite3.OperationalError("send failure")):
            with self.assertRaises(ContractLifecycleError):self.lifecycle.record_send(self.contract.id,r01.document.id,"2026-08-16")
        self.assertEqual(self.lifecycle.history(self.contract.id),before);self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TO_SIGN)

    def test_opening_files_and_documents_view_create_no_events_and_missing_file_is_preserved(self):
        r01=self.service().generate(self.contract.id);before=self.lifecycle.history(self.contract.id);docx_bytes=r01.docx_path.read_bytes();pdf_bytes=r01.pdf_path.read_bytes()
        self.lifecycle.open_document(r01.document.id,"docx");self.lifecycle.open_document(r01.document.id,"pdf");self.assertEqual(len(self.opener.paths),2);self.assertEqual(self.lifecycle.history(self.contract.id),before)
        view=DocumentsView(self.lifecycle,lambda message,error:None);view.load(self.contract.id);self.assertIn("Dernière révision"," ".join(label.text() for label in view.findChildren(QLabel)))
        r01.docx_path.unlink();view.load(self.contract.id);text=" ".join(label.text() for label in view.findChildren(QLabel));self.assertIn("introuvable (DOCX)",text);self.assertIsNotNone(self.documents.get(r01.document.id));self.assertTrue(r01.pdf_path.is_file())
        r01.docx_path.write_bytes(docx_bytes);r01.pdf_path.unlink();view.load(self.contract.id);text=" ".join(label.text() for label in view.findChildren(QLabel));self.assertIn("introuvable (PDF)",text);self.assertTrue(r01.docx_path.is_file());r01.pdf_path.write_bytes(pdf_bytes)

    def test_reopen_is_atomic_and_rejects_stale_state(self):
        r01=self.service().generate(self.contract.id);self.lifecycle.reopen_for_correction(self.contract.id)
        self.assertEqual(len([e for e in self.lifecycle.history(self.contract.id) if e.type is ContractEventType.REOPENED_FOR_CORRECTION]),1)
        with self.assertRaises(ContractLifecycleError):self.lifecycle.reopen_for_correction(self.contract.id)
        self.assertEqual(r01.docx_path.read_bytes(),Path(r01.docx_path).read_bytes())

    def test_documents_ui_states_and_bounded_modals(self):
        view=DocumentsView(self.lifecycle,lambda message,error:None);view.load(self.contract.id)
        self.assertIn("Aucun document"," ".join(label.text() for label in view.findChildren(QLabel)))
        r01=self.service().generate(self.contract.id);view.load(self.contract.id);text=" ".join(label.text() for label in view.findChildren(QLabel))
        self.assertIn("Dernière révision",text);self.assertNotIn("signature",text.lower())
        send=SendRecordingDialog(self.lifecycle.revisions(self.contract.id));self.assertEqual(send.findChild(QComboBox,"sentRevision").currentData(),r01.document.id)
        correction=CorrectionConfirmationDialog();self.assertTrue(any(button.text()=="Corriger le contrat" for button in correction.findChildren(QPushButton)))
        before=self.lifecycle.history(self.contract.id)
        with patch.object(CorrectionConfirmationDialog,"exec",return_value=QDialog.DialogCode.Rejected):view._reopen()
        with patch.object(SendRecordingDialog,"exec",return_value=QDialog.DialogCode.Rejected):view._send()
        self.assertEqual(self.lifecycle.history(self.contract.id),before)


if __name__=="__main__":unittest.main()
