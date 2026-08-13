from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication,QComboBox,QDialog,QLabel,QLineEdit,QPushButton

from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.domain import ContractEventType,ContractStatus,SignedCopyState
from icp_renov_contracts.errors import ContractLifecycleError
from icp_renov_contracts.repositories import ContractDocumentRepository,ContractEventRepository
from icp_renov_contracts.services import ContractLifecycleService
from icp_renov_contracts.ui.documents_view import DocumentsView,SignatureRecordingDialog

from test_document_generation import GenerationCase
from test_contract_events import RecordingOpener,UnavailableAllocator
from test_foundation import scratch


class MutableDateProvider:
    def __init__(self,value:date):self.value=value
    def today(self):return self.value


class MigrationSevenTests(unittest.TestCase):
    def test_migration_seven_applies_over_six_once(self):
        with scratch() as temporary:
            database=DatabaseService(Path(temporary)/"s6.sqlite3")
            with database.transaction() as connection:
                for migration in MIGRATIONS[:6]:
                    for statement in migration.statements:connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations VALUES (?,?)",(migration.version,"frozen"))
            database.initialize();database.initialize()
            with database.connection() as connection:
                columns=[row[1] for row in connection.execute("PRAGMA table_info(contract_documents)")]
                self.assertEqual(columns.count("signed_pdf_path"),1);self.assertEqual(columns.count("signed_pdf_hash"),1);self.assertEqual(columns.count("signed_pdf_attached_at"),1)
                self.assertIn("lifecycle_status",[row[1] for row in connection.execute("PRAGMA table_info(contracts)")])
            self.assertEqual(database.schema_version(),7)


class SignatureLifecycleTests(GenerationCase):
    @classmethod
    def setUpClass(cls):cls.application=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp();self.clock=MutableDateProvider(date(2026,8,13));self.events=ContractEventRepository(self.context.database);self.opener=RecordingOpener()
        self.lifecycle=ContractLifecycleService(self.context.database,self.contracts,self.documents,self.events,self.context.workspace.root,self.opener,self.clock,lambda:"2026-08-13T10:00:00+00:00")

    def _set_start(self,value:str):
        conditions=self.contracts.get_conditions(self.contract.id)
        self.contracts.save_conditions(self.contract.id,replace(conditions,start_date=value))

    def _two_revisions(self,r01_start="2026-08-14",r02_start="2026-08-12"):
        self._set_start(r01_start);service=self.service();r01=service.generate(self.contract.id)
        self.lifecycle.reopen_for_correction(self.contract.id);self._set_start(r02_start);service.number_allocator=UnavailableAllocator();r02=service.generate(self.contract.id)
        return r01,r02,service

    def _pdf(self,name="signed.pdf",marker=b"one"):
        path=self.context.workspace.root.parent/name
        path.write_bytes(b"%PDF-1.4\n"+marker+b"\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")
        return path

    def _types(self):return [event.type for event in reversed(self.lifecycle.history(self.contract.id))]

    def test_eligibility_exact_older_revision_and_signed_snapshot_authority(self):
        with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(self.contract.id,"missing","2026-08-13")
        r01,r02,service=self._two_revisions();r01_bytes=r01.docx_path.read_bytes();r02_bytes=r02.docx_path.read_bytes();number=r01.contract_number
        authority=self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13")
        self.assertEqual(authority.document.id,r01.document.id);self.assertEqual(authority.start_date,date(2026,8,14))
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.SIGNED);self.assertEqual(self.contracts.get(self.contract.id).signature_date,"2026-08-13")
        self.assertEqual(self.lifecycle.signature_date(self.contract.id),"2026-08-13");self.assertEqual(self._types().count(ContractEventType.SIGNATURE_RECORDED),1);self.assertNotIn(ContractEventType.ACTIVATED,self._types())
        self.assertEqual(r01.docx_path.read_bytes(),r01_bytes);self.assertEqual(r02.docx_path.read_bytes(),r02_bytes);self.assertEqual(self.contracts.get(self.contract.id).number,number)
        with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(self.contract.id,r02.document.id,"2026-08-13")
        with self.assertRaises(ContractLifecycleError):self.lifecycle.reopen_for_correction(self.contract.id)
        with self.assertRaises(DocumentGenerationError):service.generate(self.contract.id)
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)],["R01","R02"])

    def test_immediate_activation_and_future_reconciliation_are_atomic_idempotent(self):
        self._set_start("2026-08-13");r01=self.service().generate(self.contract.id);self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-12")
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE)
        events=list(reversed(self.lifecycle.history(self.contract.id)));types=[event.type for event in events]
        self.assertLess(types.index(ContractEventType.SIGNATURE_RECORDED),types.index(ContractEventType.ACTIVATED))
        activated=next(event for event in events if event.type is ContractEventType.ACTIVATED);self.assertEqual(activated.effective_date,"2026-08-13")
        self.assertEqual(self.lifecycle.reconcile_due_activations(),());self.assertEqual(self._types().count(ContractEventType.ACTIVATED),1)

    def test_future_activation_uses_signed_r01_not_latest_or_live_contract(self):
        r01,r02,_=self._two_revisions("2026-08-14","2026-08-12");self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13")
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.SIGNED);self.assertEqual(self.lifecycle.reconcile_due_activations(date(2026,8,13)),())
        self.assertTrue(self.lifecycle._activate_if_due(self.contract.id,date(2026,8,14)));self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.ACTIVE)
        self.assertFalse(self.lifecycle._activate_if_due(self.contract.id,date(2026,8,14)));self.assertEqual(self._types().count(ContractEventType.ACTIVATED),1)

    def test_signature_without_pdf_and_later_copy_create_no_copy_event(self):
        self._set_start("2026-08-14");r01=self.service().generate(self.contract.id);self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13")
        document=self.documents.get(r01.document.id);self.assertIsNone(document.signed_pdf_path);before=self.lifecycle.history(self.contract.id)
        source=self._pdf();original=source.read_bytes();updated=self.lifecycle.add_signed_copy(self.contract.id,source)
        self.assertEqual(source.read_bytes(),original);self.assertFalse(Path(updated.signed_pdf_path).is_absolute());self.assertIs(self.lifecycle.signed_copy_state(updated),SignedCopyState.VALID)
        archived=self.context.workspace.root/updated.signed_pdf_path;self.assertTrue(archived.is_file());self.assertEqual(updated.signed_pdf_hash,hashlib.sha256(original).hexdigest());self.assertIsNotNone(updated.signed_pdf_attached_at)
        self.assertEqual(self.lifecycle.history(self.contract.id),before)

    def test_invalid_pdf_and_database_failure_leave_no_signature_or_artifact(self):
        self._set_start("2026-08-14");r01=self.service().generate(self.contract.id);invalid=self.context.workspace.root.parent/"invalid.pdf";invalid.write_bytes(b"not pdf")
        with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13",invalid)
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TO_SIGN);self.assertNotIn(ContractEventType.SIGNATURE_RECORDED,self._types())
        source=self._pdf()
        with patch.object(ContractDocumentRepository,"set_signed_copy",side_effect=sqlite3.OperationalError("db")):
            with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13",source)
        self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TO_SIGN);self.assertNotIn(ContractEventType.SIGNATURE_RECORDED,self._types())
        signed_dir=r01.docx_path.parent/"signed";self.assertFalse(any(signed_dir.glob("*.pdf")) if signed_dir.exists() else False)

    def test_collision_cross_contract_and_post_signature_sending_are_bounded(self):
        self._set_start("2026-08-14");r01=self.service().generate(self.contract.id);source=self._pdf();existing=r01.docx_path.parent/"signed"/"occupied.pdf";existing.parent.mkdir(parents=True);existing.write_bytes(b"preserve")
        with patch.object(self.lifecycle,"_new_signed_destination",return_value=existing):
            with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13",source)
        self.assertEqual(existing.read_bytes(),b"preserve");self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.TO_SIGN)
        other=self.contracts.create_draft()
        with self.assertRaises(ContractLifecycleError):self.lifecycle.record_signature(other.id,r01.document.id,"2026-08-13")
        self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13")
        event=self.lifecycle.record_send(self.contract.id,r01.document.id,"2026-08-13")
        self.assertIs(event.type,ContractEventType.CONTRACT_SENT);self.assertIs(self.contracts.get(self.contract.id).status,ContractStatus.SIGNED)
        with self.assertRaises(Exception):self.contracts.update_signatory(self.contract.id,"Changed","Changed")

    def test_missing_mismatch_locate_and_replace_preserve_signature(self):
        self._set_start("2026-08-14");r01=self.service().generate(self.contract.id);source=self._pdf();self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13",source)
        before=self.lifecycle.history(self.contract.id);document=self.documents.get(r01.document.id);attached=document.signed_pdf_attached_at;digest=document.signed_pdf_hash
        archived=self.context.workspace.root/document.signed_pdf_path;archived.unlink();self.assertIs(self.lifecycle.signed_copy_state(self.documents.get(document.id)),SignedCopyState.MISSING)
        wrong=self._pdf("wrong.pdf",b"wrong")
        with self.assertRaises(ContractLifecycleError):self.lifecycle.locate_signed_copy(self.contract.id,wrong)
        restored=self.lifecycle.locate_signed_copy(self.contract.id,source);self.assertEqual(restored.signed_pdf_hash,digest);self.assertEqual(restored.signed_pdf_attached_at,attached)
        restored_path=self.context.workspace.root/restored.signed_pdf_path;restored_path.write_bytes(wrong.read_bytes());self.assertIs(self.lifecycle.signed_copy_state(self.documents.get(document.id)),SignedCopyState.HASH_MISMATCH)
        replaced=self.lifecycle.replace_signed_copy(self.contract.id,wrong);self.assertNotEqual(replaced.signed_pdf_path,restored.signed_pdf_path);self.assertNotEqual(replaced.signed_pdf_hash,digest)
        self.assertEqual(self.lifecycle.history(self.contract.id),before)

    def test_core_document_immutability_and_narrow_metadata_update(self):
        self._set_start("2026-08-14");r01=self.service().generate(self.contract.id)
        with self.context.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE contract_documents SET pdf_sha256='changed' WHERE id=?",(r01.document.id,))
            ContractDocumentRepository.set_signed_copy(connection,r01.document.id,"x.pdf","digest","2026-08-13T10:00:00+00:00")
        self.assertEqual(self.documents.get(r01.document.id).signed_pdf_hash,"digest")
        with self.context.database.transaction() as connection:
            event=next(event for event in self.lifecycle.history(self.contract.id) if event.type is ContractEventType.DOCUMENT_GENERATED)
            with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE contract_events SET note='x' WHERE id=?",(event.id,))

    def test_signature_ui_latest_default_warning_states_and_no_manual_activate(self):
        r01,r02,_=self._two_revisions();dialog=SignatureRecordingDialog(self.lifecycle.revisions(self.contract.id),self.clock.today())
        self.assertEqual(dialog.findChild(QComboBox,"signedRevision").currentData(),r02.document.id)
        self.assertEqual(dialog.findChild(QLineEdit,"signedStartDate").text(),"12/08/2026")
        dialog.revision.setCurrentIndex(1);self.assertIn("R01",dialog.warning.text());self.assertIn("signature réalisée hors",dialog.findChildren(QLabel)[1].text())
        before=self.lifecycle.history(self.contract.id);dialog.reject();self.assertEqual(self.lifecycle.history(self.contract.id),before)
        self.lifecycle.record_signature(self.contract.id,r01.document.id,"2026-08-13");view=DocumentsView(self.lifecycle,lambda message,error:None);view.load(self.contract.id)
        text=" ".join(label.text() for label in view.findChildren(QLabel));buttons=[button.text() for button in view.findChildren(QPushButton)]
        self.assertIn("Révision signée",text);self.assertIn("Dernière révision",text);self.assertIn("copie signée non archivée",text);self.assertIn("Prise d’effet prévue le 14/08/2026",text)
        self.assertIn("Ajouter le PDF signé",buttons);self.assertNotIn("Activer",buttons);self.assertNotIn("Corriger le contrat",buttons);self.assertNotIn("Enregistrer la signature",buttons)


if __name__=="__main__":unittest.main()
