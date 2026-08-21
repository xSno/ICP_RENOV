from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
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
from icp_renov_contracts.services import ContractLifecycleService
from icp_renov_contracts.ui.web_host import UiBridge

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
        state = self._state(); row = state["documents_d1"]["revisions"][0]
        self.assertEqual(row["signed_copy_state"], SignedCopyState.MISSING.value)
        self.assertEqual(state["documents_d2"]["signature"]["revision"], "R01")
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.SIGNED)
        self.assertTrue(self._select("LOCATE", source)["selected"])
        self.assertTrue(self.bridge.locateContractSignedPdf(self.contract.id)["ok"])
        reopened = self.documents.get(r01.document.id)
        (self.context.workspace.root / reopened.signed_pdf_path).unlink()
        replacement = self._pdf("replacement.pdf", b"replacement")
        self.assertTrue(self._select("REPLACE", replacement)["selected"])
        self.assertTrue(self.bridge.replaceContractSignedPdf(self.contract.id)["ok"])
        self.assertEqual(self._events(), before)
        self.assertEqual(len(self.lifecycle.revisions(self.contract.id)), 1)

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
        for copy in ("Enregistrer la signature", "Révision réellement signée", "Ajouter le PDF signé", "Localiser le fichier", "Ajouter une nouvelle copie"):
            self.assertIn(copy, js)
        # D3/D4 may add their bounded renewal and end-of-contract controls;
        # the signed-copy surface still exposes neither interventions nor a
        # manual activation action.
        for forbidden in ("Créer une fiche d’intervention", "Activer"):
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
