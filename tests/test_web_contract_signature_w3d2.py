from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import json
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import ContractDocument, ContractEventType, ContractStatus, DocumentKind, SignedCopyState
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.errors import ContractLifecycleError
from icp_renov_contracts.repositories import ContractDocumentRepository, ContractEventRepository
from icp_renov_contracts.services import BackupService, ContractLifecycleService, RestoreService
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import MachineConfigStore
from icp_renov_contracts.ui.web_host import UiBridge
from icp_renov_contracts.services.signed_copy_replacement import decode_signed_pdf_replacement, resolve_signed_copy_audit_path

from test_contract_events import RecordingOpener, UnavailableAllocator
from test_document_generation import GenerationCase
from test_review import FakeCapabilities


class MutableDateProvider:
    def __init__(self, value: date): self.value = value
    def today(self): return self.value


class WebContractSignatureW3D2Tests(GenerationCase):
    def setUp(self):
        super().setUp()
        self.clock = MutableDateProvider(date(2026, 8, 13))
        self.review.capability_probe = FakeCapabilities(True, True)
        self.generation = self.service()
        self.opener = RecordingOpener()
        self.lifecycle = ContractLifecycleService(
            self.context.database, self.contracts, self.documents,
            ContractEventRepository(self.context.database), self.context.workspace.root,
            self.opener, self.clock, lambda: "2026-08-13T10:00:00+00:00",
        )
        self.context = replace(self.context, review=self.review, generation=self.generation, lifecycle=self.lifecycle)
        self.bridge = UiBridge(self.context, lambda _destination: None)
        self.bridge.openContract(self.contract.id)

    def _set_start(self, value: str):
        self.contracts.save_conditions(self.contract.id, replace(self.contracts.get_conditions(self.contract.id), start_date=value))

    def _generate(self): return self.generation.generate(self.contract.id)

    def _state(self): return self.bridge.contract_workspace_snapshot()

    def _events(self): return self.lifecycle.history(self.contract.id)

    def _pdf(self, name="signed.pdf", payload=b"signed"):
        path = self.context.workspace.root.parent / name
        path.write_bytes(b"%PDF-1.4\n" + payload + b"\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")
        return path

    def _select(self, intent: str, path: Path):
        with patch("icp_renov_contracts.ui.web_host.QFileDialog.getOpenFileName", return_value=(str(path), "Documents PDF (*.pdf)")):
            return self.bridge.selectContractSignedPdf(self.contract.id, intent)

    def _sign_without_copy(self):
        self._set_start("2026-08-14")
        r01 = self._generate()
        self.assertTrue(self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-13")["ok"])
        return r01

    def test_to_sign_exposes_signature_and_modal_copy_is_nonoperative_until_confirmation(self):
        self._set_start("2026-08-14")
        self._generate()
        before = self._events()
        state = self._state()
        self.assertTrue(state["documents_d2"]["signature_allowed"])
        self.assertIsNone(state["documents_d2"]["signature"])
        self.assertEqual(self.context.contracts.get(self.contract.id).signature_date, None)
        self.assertEqual(self._events(), before)

    def test_signature_requires_exact_contract_revision_and_projects_event_date_and_signed_marker(self):
        self._set_start("2026-08-14")
        r01 = self._generate()
        self.assertFalse(self.bridge.recordContractSignature(self.contract.id, "R99", "2026-08-13")["ok"])
        self.assertFalse(self.bridge.recordContractSignature(self.contract.id, "R01", "not-a-date")["ok"])
        result = self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-13")
        self.assertTrue(result["ok"])
        event = next(item for item in self._events() if item.type is ContractEventType.SIGNATURE_RECORDED)
        self.assertEqual((event.document_id, event.effective_date), (r01.document.id, "2026-08-13"))
        state = self._state()
        self.assertEqual(self.context.contracts.get(self.contract.id).signature_date, "2026-08-13")
        self.assertEqual(state["documents_d2"]["signature"]["revision"], "R01")
        self.assertEqual(state["documents_d1"]["revisions"][0]["signed_display"], "13/08/2026")

    def test_older_exact_revision_is_signed_without_marking_latest_and_post_signature_is_locked(self):
        self._set_start("2026-08-14")
        r01 = self._generate()
        self.lifecycle.reopen_for_correction(self.contract.id)
        self.generation.number_allocator = UnavailableAllocator()
        r02 = self._generate()
        self.assertTrue(self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-13")["ok"])
        rows = self._state()["documents_d1"]["revisions"]
        self.assertEqual([row["revision"] for row in rows], ["R02", "R01"])
        self.assertFalse(rows[0]["signed"]); self.assertTrue(rows[1]["signed"])
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.SIGNED)
        self.assertFalse(self._state()["documents_d1"]["correction_allowed"])
        self.assertFalse(self._state()["documents_d1"]["send_allowed"])
        self.assertFalse(self._state()["documents_d2"]["signature_allowed"])
        with self.assertRaises(DocumentGenerationError): self._generate()
        self.assertEqual([item.revision for item in self.lifecycle.revisions(self.contract.id)], ["R02", "R01"])
        self.assertEqual((r01.contract_number, r02.contract_number), (self.contracts.get(self.contract.id).number, self.contracts.get(self.contract.id).number))

    def test_signature_without_copy_can_attach_later_and_opening_signed_pdf_creates_no_event(self):
        r01 = self._sign_without_copy()
        before = self._events()
        state = self._state()
        row = state["documents_d1"]["revisions"][0]
        self.assertEqual(row["signed_copy_state"], SignedCopyState.NONE.value)
        source = self._pdf()
        self.assertTrue(self._select("ADD", source)["selected"])
        self.assertTrue(self.bridge.addContractSignedPdf(self.contract.id)["ok"])
        document = self.documents.get(r01.document.id)
        self.assertIs(self.lifecycle.signed_copy_state(document), SignedCopyState.VALID)
        self.assertTrue(self.bridge.openContractDocument(self.contract.id, "R01", "signed")["ok"])
        self.assertEqual(self._events(), before)
        self.assertEqual(self.opener.paths[-1], self.context.workspace.root / document.signed_pdf_path)
        self.assertTrue((self.context.workspace.root / r01.document.pdf_relpath).is_file())

    def test_optional_copy_failure_keeps_signature_and_history_is_preserved_when_copy_later_goes_missing(self):
        self._set_start("2026-08-14")
        r01 = self._generate()
        source = self._pdf("optional.pdf", b"optional")
        self.assertTrue(self._select("SIGNATURE", source)["selected"])
        with patch.object(ContractDocumentRepository, "set_signed_copy", side_effect=sqlite3.OperationalError("db")):
            self.assertTrue(self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-13")["ok"])
        document = self.documents.get(r01.document.id)
        state = self._state(); row = state["documents_d1"]["revisions"][0]
        self.assertEqual(sum(event.type is ContractEventType.SIGNATURE_RECORDED for event in self._events()), 1)
        self.assertEqual((self.context.contracts.get(self.contract.id).status, self.context.contracts.get(self.contract.id).signature_date), (ContractStatus.SIGNED, "2026-08-13"))
        self.assertTrue(row["signed"]); self.assertEqual(row["signed_copy_state"], SignedCopyState.NONE.value)
        self.assertIsNone(document.signed_pdf_path); self.assertIsNone(document.signed_pdf_hash); self.assertEqual(len(self.lifecycle.revisions(self.contract.id)), 1)
        self.assertTrue(self._select("ADD", source)["selected"]); self.assertTrue(self.bridge.addContractSignedPdf(self.contract.id)["ok"])
        document = self.documents.get(r01.document.id); attached_at = document.signed_pdf_attached_at
        archived_history = next(item for item in self._state()["documents_d1"]["timeline"] if item["label"] == "Copie signée archivée")
        self.assertEqual((archived_history["revision"], archived_history["occurred_display"], archived_history["documentary"]), ("R01", attached_at and datetime.fromisoformat(attached_at).strftime("%d/%m/%Y %H:%M"), True))
        (self.context.workspace.root / document.signed_pdf_path).unlink()
        missing_state = self._state(); missing_row = missing_state["documents_d1"]["revisions"][0]
        self.assertEqual(missing_row["signed_copy_state"], SignedCopyState.MISSING.value)
        self.assertIsNone(missing_state["documents_d1"]["feedback"])
        self.assertTrue(any(item["label"] == "Copie signée archivée" for item in missing_state["documents_d1"]["timeline"]))
        self.assertEqual(sum(event.type is ContractEventType.SIGNATURE_RECORDED for event in self._events()), 1)
        self.assertEqual((self.context.contracts.get(self.contract.id).status, missing_row["signed"]), (ContractStatus.SIGNED, True))

    def test_missing_signed_copy_is_distinct_and_locate_or_replace_preserve_signature_history(self):
        r01 = self._sign_without_copy()
        source = self._pdf("original.pdf", b"original")
        self._select("ADD", source); self.assertTrue(self.bridge.addContractSignedPdf(self.contract.id)["ok"])
        document = self.documents.get(r01.document.id)
        archived = self.context.workspace.root / document.signed_pdf_path
        archived.unlink()
        before = self._events()
        before_metadata = self.documents.get(r01.document.id)
        before_files = set(archived.parent.glob("*.pdf"))
        state = self._state(); row = state["documents_d1"]["revisions"][0]
        self.assertEqual(row["signed_copy_state"], SignedCopyState.MISSING.value)
        self.assertEqual(state["documents_d2"]["signature"]["revision"], "R01")
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.SIGNED)
        with self.assertRaises(ContractLifecycleError) as guidance:
            self.lifecycle.replace_signed_copy(self.contract.id, source)
        self.assertIn("Localiser", guidance.exception.user_message)
        self.assertEqual(self.documents.get(r01.document.id), before_metadata)
        self.assertEqual(set(archived.parent.glob("*.pdf")), before_files)
        self.assertEqual(self._events(), before)
        self.assertTrue(self._select("LOCATE", source)["selected"])
        self.assertTrue(self.bridge.locateContractSignedPdf(self.contract.id)["ok"])
        reopened = self.documents.get(r01.document.id)
        (self.context.workspace.root / reopened.signed_pdf_path).unlink()
        replacement = self._pdf("replacement.pdf", b"replacement")
        mismatch_files = set((self.context.workspace.root / reopened.signed_pdf_path).parent.glob("*.pdf"))
        mismatch_events = self._events()
        with self.assertRaises(ContractLifecycleError) as guidance:
            self.lifecycle.replace_signed_copy(self.contract.id, source)
        self.assertIn("Localiser", guidance.exception.user_message)
        current = self.documents.get(r01.document.id)
        self.assertEqual((current.signed_pdf_hash, current.signed_pdf_attached_at), (reopened.signed_pdf_hash, reopened.signed_pdf_attached_at))
        self.assertEqual(set((self.context.workspace.root / reopened.signed_pdf_path).parent.glob("*.pdf")), mismatch_files)
        self.assertEqual(self._events(), mismatch_events)
        self.assertTrue(self._select("LOCATE", source)["selected"])
        self.assertTrue(self.bridge.locateContractSignedPdf(self.contract.id)["ok"])
        self.assertTrue(self._select("REPLACE", replacement)["selected"])
        self.assertTrue(self.bridge.replaceContractSignedPdf(self.contract.id)["ok"])
        replacements = [event for event in self._events() if event.type is ContractEventType.ADMIN_CORRECTION and event.reason_code == "SIGNED_PDF_REPLACED"]
        self.assertEqual(len(replacements), 1)
        self.assertEqual(replacements[0].document_id, r01.document.id)
        self.assertEqual(len(self.lifecycle.revisions(self.contract.id)), 1)

    def test_valid_signed_copy_replacement_preserves_authority_and_audits_previous_copy(self):
        r01 = self._sign_without_copy()
        original = self._pdf("original-valid.pdf", b"original-valid")
        self.assertTrue(self._select("ADD", original)["selected"])
        self.assertTrue(self.bridge.addContractSignedPdf(self.contract.id)["ok"])
        before = self.documents.get(r01.document.id)
        old_path = self.context.workspace.root / before.signed_pdf_path
        before_files = set(old_path.parent.glob("*.pdf"))
        self.assertTrue(old_path.is_file())
        self.lifecycle.now_provider = lambda: "2026-08-14T12:00:00+00:00"
        replacement = self._pdf("replacement-valid.pdf", b"replacement-valid")
        self.assertTrue(self._select("REPLACE", replacement)["selected"])
        result = self.bridge.replaceContractSignedPdf(self.contract.id)
        self.assertTrue(result["ok"])
        after = self.documents.get(r01.document.id)
        self.assertIs(self.lifecycle.signed_copy_state(after), SignedCopyState.VALID)
        self.assertTrue(old_path.is_file())
        self.assertNotEqual(after.signed_pdf_path, before.signed_pdf_path)
        self.assertEqual(self.context.contracts.get(self.contract.id).signature_date, "2026-08-13")
        self.assertEqual(sum(event.type is ContractEventType.SIGNATURE_RECORDED for event in self._events()), 1)
        event = next(event for event in self._events() if event.reason_code == "SIGNED_PDF_REPLACED")
        audit = decode_signed_pdf_replacement(event.note)
        self.assertIsNotNone(audit)
        self.assertEqual((event.document_id, event.occurred_at, after.signed_pdf_attached_at), (r01.document.id, "2026-08-14T12:00:00+00:00", "2026-08-14T12:00:00+00:00"))
        self.assertEqual((audit.previous_path, audit.previous_attached_at, audit.replacement_path, audit.replacement_attached_at), (before.signed_pdf_path, before.signed_pdf_attached_at, after.signed_pdf_path, after.signed_pdf_attached_at))
        timeline = self._state()["documents_d1"]["timeline"]
        self.assertTrue(any(item["label"] == "PDF signé remplacé" and item["revision"] == "R01" for item in timeline))

        events_before_noop = self._events()
        no_change = self.lifecycle.replace_signed_copy(self.contract.id, replacement)
        self.assertEqual(no_change.kind, "SAME_CONTENT")
        self.assertEqual(self.documents.get(r01.document.id), after)
        self.assertEqual(self._events(), events_before_noop)
        self.assertEqual(set(old_path.parent.glob("*.pdf")), before_files | {self.context.workspace.root / after.signed_pdf_path})

    def test_replacement_cas_or_event_failure_preserves_previous_copy(self):
        r01 = self._sign_without_copy()
        original = self._pdf("cas-original.pdf", b"cas-original")
        self._select("ADD", original); self.bridge.addContractSignedPdf(self.contract.id)
        before = self.documents.get(r01.document.id)
        old_path = self.context.workspace.root / before.signed_pdf_path
        before_files = set(old_path.parent.glob("*.pdf"))
        replacement = self._pdf("cas-replacement.pdf", b"cas-replacement")
        with patch.object(ContractDocumentRepository, "replace_signed_copy_cas", return_value=False):
            with self.assertRaises(ContractLifecycleError):
                self.lifecycle.replace_signed_copy(self.contract.id, replacement)
        self.assertEqual(self.documents.get(r01.document.id), before)
        self.assertTrue(old_path.is_file())
        self.assertEqual(set(old_path.parent.glob("*.pdf")), before_files)
        self.assertFalse(any(event.reason_code == "SIGNED_PDF_REPLACED" for event in self._events()))

        with patch.object(ContractEventRepository, "insert", side_effect=sqlite3.IntegrityError("event")):
            with self.assertRaises(ContractLifecycleError):
                self.lifecycle.replace_signed_copy(self.contract.id, replacement)
        self.assertEqual(self.documents.get(r01.document.id), before)
        self.assertTrue(old_path.is_file())
        self.assertEqual(set(old_path.parent.glob("*.pdf")), before_files)
        self.assertFalse(any(event.reason_code == "SIGNED_PDF_REPLACED" for event in self._events()))

    def test_replacement_audit_and_both_signed_copies_survive_backup_restore(self):
        r01 = self._sign_without_copy()
        original = self._pdf("portable-original.pdf", b"portable-original")
        self._select("ADD", original); self.assertTrue(self.bridge.addContractSignedPdf(self.contract.id)["ok"])
        before = self.documents.get(r01.document.id)
        replacement = self._pdf("portable-replacement.pdf", b"portable-replacement")
        self._select("REPLACE", replacement); self.assertTrue(self.bridge.replaceContractSignedPdf(self.contract.id)["ok"])
        after = self.documents.get(r01.document.id)
        machine_store = MachineConfigStore(self.temporary / "bootstrap.json")
        backup = BackupService(self.context.database, self.context.workspace, machine_store)
        backup.set_destination(self.temporary / "backup")
        archive = backup.create_now()
        restored = RestoreService(machine_store).restore(archive, self.temporary / "restored")
        restored_context = build_application_context(config_store=machine_store)
        restored_document = ContractDocumentRepository(restored_context.database).get(r01.document.id)
        self.assertEqual(restored.root, restored_context.workspace.root)
        self.assertEqual((restored_document.signed_pdf_path, restored_document.signed_pdf_hash), (after.signed_pdf_path, after.signed_pdf_hash))
        restored_lifecycle = ContractLifecycleService(restored_context.database, restored_context.contracts, ContractDocumentRepository(restored_context.database), ContractEventRepository(restored_context.database), restored.root, self.opener, self.clock, lambda: "2026-08-13T10:00:00+00:00")
        self.assertIs(restored_lifecycle.signed_copy_state(restored_document), SignedCopyState.VALID)
        restored_events = ContractEventRepository(restored_context.database).list_for_contract(self.contract.id)
        replacement_event = next(event for event in restored_events if event.reason_code == "SIGNED_PDF_REPLACED")
        audit = decode_signed_pdf_replacement(replacement_event.note)
        self.assertEqual((audit.previous_path, audit.replacement_path), (before.signed_pdf_path, after.signed_pdf_path))
        previous_file = resolve_signed_copy_audit_path(restored.root, audit.previous_path)
        replacement_file = resolve_signed_copy_audit_path(restored.root, audit.replacement_path)
        self.assertTrue(previous_file.is_file()); self.assertTrue(replacement_file.is_file())
        self.assertEqual(hashlib.sha256(previous_file.read_bytes()).hexdigest(), audit.previous_hash)
        self.assertEqual(hashlib.sha256(replacement_file.read_bytes()).hexdigest(), audit.replacement_hash)
        self.assertEqual(restored_document.signed_pdf_hash, audit.replacement_hash)
        self.assertNotIn(str(self.context.workspace.root), replacement_event.note); self.assertNotIn(str(restored.root), replacement_event.note)
        self.assertEqual(restored_lifecycle.signature_authority(self.contract.id).document.id, r01.document.id)
        self.assertEqual(sum(event.type is ContractEventType.SIGNATURE_RECORDED for event in restored_events), 1)

    def test_multiple_replacements_keep_audit_chain_and_prior_files(self):
        r01 = self._sign_without_copy()
        source_a = self._pdf("chain-a.pdf", b"chain-a")
        self._select("ADD", source_a); self.bridge.addContractSignedPdf(self.contract.id)
        document_a = self.documents.get(r01.document.id)
        self.lifecycle.now_provider = lambda: "2026-08-14T11:00:00+00:00"
        source_b = self._pdf("chain-b.pdf", b"chain-b"); document_b = self.lifecycle.replace_signed_copy(self.contract.id, source_b).document
        self.lifecycle.now_provider = lambda: "2026-08-14T12:00:00+00:00"
        source_c = self._pdf("chain-c.pdf", b"chain-c"); document_c = self.lifecycle.replace_signed_copy(self.contract.id, source_c).document
        audits = [decode_signed_pdf_replacement(event.note) for event in self._events() if event.reason_code == "SIGNED_PDF_REPLACED"]
        self.assertEqual(len(audits), 2)
        self.assertEqual((audits[0].previous_path, audits[0].replacement_path), (document_b.signed_pdf_path, document_c.signed_pdf_path))
        self.assertEqual((audits[1].previous_path, audits[1].replacement_path), (document_a.signed_pdf_path, document_b.signed_pdf_path))
        self.assertEqual(audits[1].previous_attached_at, document_a.signed_pdf_attached_at)
        self.assertEqual(audits[0].previous_attached_at, document_b.signed_pdf_attached_at)
        self.assertTrue((self.context.workspace.root / document_a.signed_pdf_path).is_file())
        self.assertTrue((self.context.workspace.root / document_b.signed_pdf_path).is_file())
        self.assertIs(self.lifecycle.signed_copy_state(document_c), SignedCopyState.VALID)

    def test_real_cas_rejects_stale_metadata_without_overwriting_newer_value(self):
        r01 = self._sign_without_copy()
        source_a = self._pdf("stale-a.pdf", b"stale-a")
        self._select("ADD", source_a); self.bridge.addContractSignedPdf(self.contract.id)
        document_a = self.documents.get(r01.document.id)
        source_b = self._pdf("stale-b.pdf", b"stale-b")
        digest_b = hashlib.sha256(source_b.read_bytes()).hexdigest()
        with self.context.database.transaction() as connection:
            self.assertTrue(ContractDocumentRepository.replace_signed_copy_cas(connection, document_a.id, document_a.signed_pdf_path, document_a.signed_pdf_hash, document_a.signed_pdf_attached_at, "documents/contracts/stale-b.pdf", digest_b, "2026-08-14T11:00:00+00:00"))
        with self.context.database.transaction() as connection:
            self.assertFalse(ContractDocumentRepository.replace_signed_copy_cas(connection, document_a.id, document_a.signed_pdf_path, document_a.signed_pdf_hash, document_a.signed_pdf_attached_at, "documents/contracts/stale-c.pdf", "c" * 64, "2026-08-14T12:00:00+00:00"))
        current = self.documents.get(document_a.id)
        self.assertEqual((current.signed_pdf_path, current.signed_pdf_hash), ("documents/contracts/stale-b.pdf", digest_b))

    def test_replacement_payload_rejects_nonportable_paths(self):
        digest = "a" * 64
        def payload(path, previous_at="2026-08-13T10:00:00+00:00", replacement_at="2026-08-14T10:00:00+00:00", state="VALID"): return json.dumps({"version": "SIGNED_PDF_REPLACEMENT_V1", "previous": {"path": path, "sha256": digest, "attached_at": previous_at, "state": state}, "replacement": {"path": "documents/contracts/id/R01/signed/signed-abc.pdf", "sha256": digest, "attached_at": replacement_at}})
        for path in (".", "./file.pdf", "foo/./bar.pdf", "foo//bar.pdf", "../outside.pdf", "/absolute/file.pdf", r"C:\\Users\\Test\\file.pdf", "C:/Users/Test/file.pdf", r"\\\\server\\share\\file.pdf"):
            self.assertIsNone(decode_signed_pdf_replacement(payload(path)))
        self.assertIsNone(decode_signed_pdf_replacement(payload("documents/contracts/id/R01/signed/signed-abc.pdf", previous_at="invalid")))
        self.assertIsNone(decode_signed_pdf_replacement(payload("documents/contracts/id/R01/signed/signed-abc.pdf", replacement_at="invalid")))
        self.assertIsNone(decode_signed_pdf_replacement(payload("documents/contracts/id/R01/signed/signed-abc.pdf", state="OTHER")))
        self.assertIsNotNone(decode_signed_pdf_replacement(payload("documents/contracts/id/R01/signed/signed-abc.pdf")))

    def test_hash_mismatch_expected_content_uses_locate_without_replacement(self):
        r01 = self._sign_without_copy()
        source_a = self._pdf("mismatch-a.pdf", b"mismatch-a")
        self._select("ADD", source_a); self.bridge.addContractSignedPdf(self.contract.id)
        before = self.documents.get(r01.document.id); signed_file = self.context.workspace.root / before.signed_pdf_path
        source_b = self._pdf("mismatch-b.pdf", b"mismatch-b")
        signed_file.write_bytes(source_b.read_bytes())
        self.assertIs(self.lifecycle.signed_copy_state(self.documents.get(r01.document.id)), SignedCopyState.HASH_MISMATCH)
        files = set(signed_file.parent.glob("*.pdf")); events = self._events()
        with self.assertRaises(ContractLifecycleError) as guidance: self.lifecycle.replace_signed_copy(self.contract.id, source_a)
        self.assertIn("Localiser", guidance.exception.user_message)
        self.assertEqual(self.documents.get(r01.document.id), before); self.assertEqual(set(signed_file.parent.glob("*.pdf")), files); self.assertEqual(self._events(), events)
        restored = self.lifecycle.locate_signed_copy(self.contract.id, source_a)
        self.assertIs(self.lifecycle.signed_copy_state(restored), SignedCopyState.VALID)
        self.assertEqual((restored.signed_pdf_hash, restored.signed_pdf_attached_at), (before.signed_pdf_hash, before.signed_pdf_attached_at))
        self.assertEqual(self._events(), events)

    def test_invalid_signed_pdf_failure_does_not_create_signature_or_false_state(self):
        self._set_start("2026-08-14")
        self._generate()
        invalid = self.context.workspace.root.parent / "invalid.pdf"; invalid.write_bytes(b"not a pdf")
        self._select("SIGNATURE", invalid)
        self.assertFalse(self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-13")["ok"])
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.TO_SIGN)
        self.assertFalse(any(event.type is ContractEventType.SIGNATURE_RECORDED for event in self._events()))
        self.assertTrue(self._state()["documents_d2"]["signature_allowed"])

    def test_reached_start_date_activates_without_manual_control_and_timeline_is_localized(self):
        self._set_start("2026-08-13")
        self._generate()
        self.assertTrue(self.bridge.recordContractSignature(self.contract.id, "R01", "2026-08-12")["ok"])
        state = self._state()
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.ACTIVE)
        labels = [item["label"] for item in state["documents_d1"]["timeline"]]
        self.assertIn("Signature enregistrée", labels); self.assertIn("Contrat activé", labels)
        self.assertNotIn("Activer", (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8"))

    def test_web_surface_has_signature_modal_status_gating_and_no_unsupported_lifecycle_controls(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renderContractDocuments")
        end = js.index("function renderContractReview", start)
        d2 = js[start:end]
        for copy in ("Enregistrer la signature", "Révision réellement signée", "Ajouter le PDF signé", "Localiser le fichier", "Remplacer le PDF signé"):
            self.assertIn(copy, js)
        css = (root / "contract-documents.css").read_text(encoding="utf-8")
        self.assertIn(".documents-feedback.info", css)
        # D5 may add separate intervention documents; the signed-copy surface
        # still exposes no manual activation action.
        for forbidden in ("Activer",):
            self.assertNotIn(forbidden, d2)
        engine = QJSEngine(); engine.evaluate("function esc(value){return String(value ?? '');} var contractDocumentsError='';")
        evaluated = engine.evaluate(d2); self.assertFalse(evaluated.isError(), evaluated.toString())
        base = {"documents_d1": {"feedback": None, "revisions": [], "timeline": [], "correction_allowed": False, "send_allowed": False}, "documents_d2": {"signature_allowed": True}}
        to_sign = engine.evaluate(f"renderContractDocuments({json.dumps(base)})").toString()
        base["documents_d2"]["signature_allowed"] = False
        signed = engine.evaluate(f"renderContractDocuments({json.dumps(base)})").toString()
        self.assertIn("Enregistrer la signature", to_sign); self.assertNotIn("Enregistrer la signature", signed)

    def test_intervention_sheet_cannot_be_signed(self):
        self._set_start("2026-08-14")
        self._generate()
        sheet = ContractDocument(str(uuid.uuid4()), self.contract.id, DocumentKind.INTERVENTION_SHEET, None,
            datetime.now(timezone.utc).isoformat(), self.version.id, "documents/interventions/x.docx", "documents/interventions/x.pdf", "{}", "a" * 64, "b" * 64)
        with self.context.database.transaction() as connection: ContractDocumentRepository.insert(connection, sheet)
        with self.assertRaises(ContractLifecycleError): self.lifecycle.record_signature(self.contract.id, sheet.id, "2026-08-13")


if __name__ == "__main__":
    unittest.main()
