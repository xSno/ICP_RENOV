from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import ContractDocument, ContractEventType, ContractStatus, DocumentKind
from icp_renov_contracts.repositories import ContractDocumentRepository
from icp_renov_contracts.ui.web_host import UiBridge

from test_contract_events import RecordingOpener, UnavailableAllocator
from test_document_generation import GenerationCase
from test_review import FakeCapabilities


class WebContractDocumentsW3D1Tests(GenerationCase):
    def setUp(self):
        super().setUp()
        self.review.capability_probe = FakeCapabilities(True, True)
        self.generation = self.service()
        self.context = replace(self.context, review=self.review, generation=self.generation)
        self.opener = RecordingOpener()
        self.context.lifecycle.opener = self.opener
        self.bridge = UiBridge(self.context, lambda _destination: None)
        self.bridge.openContract(self.contract.id)

    def generate(self):
        return self.generation.generate(self.contract.id)

    def events(self):
        return self.context.lifecycle.history(self.contract.id)

    def snapshot(self):
        return self.bridge.contract_workspace_snapshot()["documents_d1"]

    def insert_intervention_sheet(self):
        document = ContractDocument(
            str(uuid.uuid4()), self.contract.id, DocumentKind.INTERVENTION_SHEET, None,
            datetime.now(timezone.utc).isoformat(), self.version.id,
            "documents/interventions/sheet.docx", "documents/interventions/sheet.pdf",
            "{}", "a" * 64, "b" * 64,
        )
        with self.context.database.transaction() as connection:
            ContractDocumentRepository.insert(connection, document)
        return document

    def test_step_four_projects_persisted_contract_revisions_in_exact_order_with_template(self):
        r01 = self.generate()
        self.context.lifecycle.reopen_for_correction(self.contract.id)
        self.generation.number_allocator = UnavailableAllocator()
        r02 = self.generate()
        sheet = self.insert_intervention_sheet()

        projected = self.snapshot()["revisions"]
        self.assertEqual([item["revision"] for item in projected], ["R02", "R01"])
        self.assertTrue(projected[0]["latest"]); self.assertFalse(projected[0]["replaced"])
        self.assertFalse(projected[1]["latest"]); self.assertTrue(projected[1]["replaced"])
        self.assertEqual(projected[0]["template_name"], self.version.template_name)
        self.assertEqual(projected[0]["template_version"], self.version.version)
        self.assertNotIn(sheet.id, str(projected))
        self.assertEqual(r02.document.revision, "R02"); self.assertEqual(r01.document.revision, "R01")

    def test_open_exact_docx_pdf_creates_no_event_and_arbitrary_requests_are_rejected(self):
        r01 = self.generate()
        before = self.events()
        self.assertTrue(self.bridge.openContractDocument(self.contract.id, "R01", "docx")["ok"])
        self.assertTrue(self.bridge.openContractDocument(self.contract.id, "R01", "pdf")["ok"])
        self.assertEqual(self.opener.paths, [r01.docx_path, r01.pdf_path])
        self.assertEqual(self.events(), before)
        self.assertFalse(self.bridge.openContractDocument(self.contract.id, "R01", "../../data")["ok"])
        self.assertFalse(self.bridge.openContractDocument("another-contract", "R01", "docx")["ok"])
        self.assertEqual(self.events(), before)

    def test_missing_file_preserves_revision_and_exposes_document_anomaly(self):
        r01 = self.generate()
        r01.pdf_path.unlink()
        projected = self.snapshot()["revisions"]
        self.assertEqual([item["revision"] for item in projected], ["R01"])
        self.assertTrue(projected[0]["docx_available"])
        self.assertFalse(projected[0]["pdf_available"])
        self.assertEqual(len(self.context.lifecycle.revisions(self.contract.id)), 1)
        self.assertFalse(self.bridge.openContractDocument(self.contract.id, "R01", "pdf")["ok"])

    def test_to_sign_is_locked_and_step_four_consultation_is_event_free(self):
        self.generate()
        self.assertIs(self.context.contracts.get(self.contract.id).status, ContractStatus.TO_SIGN)
        before = self.events()
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        state = self.bridge.contract_workspace_snapshot()
        self.assertFalse(state["contract"]["editable"])
        self.assertTrue(state["documents_d1"]["send_allowed"])
        self.assertTrue(state["documents_d1"]["correction_allowed"])
        self.assertEqual(state["summary"]["completion"], "Revue complète")
        self.assertEqual(self.events(), before)
        self.assertFalse(self.bridge.updateContractSignatory(self.contract.id, "Changed", "Changed")["ok"])
        self.assertEqual(self.events(), before)

    def test_correction_is_explicit_atomic_and_preserves_number_r01_without_creating_revision(self):
        r01 = self.generate()
        number = r01.contract_number
        self.assertTrue(self.bridge.recordContractSent(self.contract.id, "R01", "2026-08-19", "Transmise au client")["ok"])
        before = self.events()
        self.bridge.contract_workspace_snapshot()
        self.assertEqual(self.events(), before)
        result = self.bridge.reopenContractForCorrection(self.contract.id)
        self.assertTrue(result["ok"])
        contract = self.context.contracts.get(self.contract.id)
        self.assertIs(contract.status, ContractStatus.DRAFT)
        self.assertEqual(contract.number, number)
        self.assertEqual([item.revision for item in self.context.lifecycle.revisions(contract.id)], ["R01"])
        reopened = [item for item in self.events() if item.type is ContractEventType.REOPENED_FOR_CORRECTION]
        self.assertEqual(len(reopened), 1)
        state = self.bridge.contract_workspace_snapshot()
        self.assertFalse(state["documents_d1"]["send_allowed"])
        self.assertEqual(state["documents_d1"]["revisions"][0]["sent_display"], "19/08/2026")
        self.assertIn("Envoi de la révision enregistré", [item["label"] for item in state["documents_d1"]["timeline"]])
        self.assertEqual(state["summary"]["completion"], "Revue complète")
        self.assertNotIn("révision(s)", state["summary"]["completion"])

    def test_next_generation_after_correction_alone_creates_r02_with_same_number(self):
        r01 = self.generate()
        self.assertTrue(self.bridge.reopenContractForCorrection(self.contract.id)["ok"])
        self.generation.number_allocator = UnavailableAllocator()
        r02 = self.generate()
        self.assertEqual((r02.contract_number, r02.document.revision), (r01.contract_number, "R02"))
        self.assertEqual([item.revision for item in self.context.lifecycle.revisions(self.contract.id)], ["R02", "R01"])

    def test_contract_sent_is_exact_revision_specific_and_does_not_propagate(self):
        r01 = self.generate()
        sent = self.bridge.recordContractSent(self.contract.id, "R01", "2026-08-19", "Transmise au client")
        self.assertTrue(sent["ok"])
        event = next(item for item in self.events() if item.type is ContractEventType.CONTRACT_SENT)
        self.assertEqual(event.document_id, r01.document.id)
        self.assertEqual(event.effective_date, "2026-08-19")
        self.assertEqual(event.note, "Transmise au client")
        self.assertFalse(self.bridge.recordContractSent(self.contract.id, "R99", "2026-08-20", "")["ok"])
        self.bridge.reopenContractForCorrection(self.contract.id)
        self.generation.number_allocator = UnavailableAllocator()
        self.generate()
        rows = self.snapshot()["revisions"]
        self.assertEqual(rows[0]["revision"], "R02"); self.assertEqual(rows[0]["sent_display"], "")
        self.assertEqual(rows[1]["revision"], "R01"); self.assertEqual(rows[1]["sent_display"], "19/08/2026")

    def test_timeline_projects_only_actual_supported_events_in_french(self):
        self.generate()
        self.bridge.recordContractSent(self.contract.id, "R01", "2026-08-19", "Note réelle")
        self.bridge.reopenContractForCorrection(self.contract.id)
        before = self.events()
        timeline = self.snapshot()["timeline"]
        self.assertEqual(self.events(), before)
        self.assertEqual([item["label"] for item in timeline], [
            "Contrat rouvert pour correction", "Envoi de la révision enregistré",
            "Révision contractuelle générée", "Contrat créé",
        ])
        serialized = str(timeline)
        for raw in ("CREATED", "DOCUMENT_GENERATED", "REOPENED_FOR_CORRECTION", "CONTRACT_SENT"):
            self.assertNotIn(raw, serialized)
        self.assertIn("Note réelle", serialized)

    def test_frontend_has_bounded_d1_modals_and_no_signature_or_lifecycle_controls(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renderContractDocuments")
        end = js.index("function renderContractReview", start)
        d1 = js[start:end]
        for copy in ("Révisions contractuelles", "Historique", "Autres documents", "Corriger le contrat", "Enregistrer un envoi"):
            self.assertIn(copy, js)
        # D4 adds its separately gated end-of-contract controls to this shared
        # Step 4 renderer; D1 itself still owns no signature state machine or activation.
        for forbidden in ("SIGNATURE_RECORDED", "Activer"):
            self.assertNotIn(forbidden, d1)
        correction = js[js.index("function openCorrectionConfirmation"):js.index("function openRecordSentModal")]
        self.assertNotIn("bridge.reopenContractForCorrection", correction.split("addEventListener", 1)[0])
        self.assertIn("bridge.reopenContractForCorrection", correction)
        abandonment = js[js.index("function openAbandonConfirmation"):js.index("function openGenerationConfirmation")]
        self.assertIn('button-danger-quiet correction-confirm-action', correction)
        self.assertIn('button-danger-quiet abandon-confirm-action', abandonment)
        self.assertIn("bridge.abandonContract", abandonment)
        self.assertIn("revision-card ${item.latest ? 'current' : ''}", d1)
        css = (root / "contract-workspace-ds01d.css").read_text(encoding="utf-8")
        for selector in (".signed-copy-notice", ".signed-copy-valid", ".signed-copy-missing", ".revision-card.current"):
            self.assertIn(selector, css)
        self.assertNotIn(".signed-copy-zone", css)
        engine = QJSEngine()
        engine.evaluate("function esc(value){return String(value ?? '');} var contractDocumentsError='';")
        evaluated = engine.evaluate(d1)
        self.assertFalse(evaluated.isError(), evaluated.toString())
        data = {
            "feedback": None, "revisions": [], "timeline": [], "correction_allowed": False,
            "send_allowed": True,
        }
        to_sign = engine.evaluate(f"renderContractDocuments({{documents_d1:{json.dumps(data)}}})").toString()
        data["send_allowed"] = False
        draft = engine.evaluate(f"renderContractDocuments({{documents_d1:{json.dumps(data)}}})").toString()
        self.assertIn("Enregistrer un envoi", to_sign)
        self.assertNotIn("Enregistrer un envoi", draft)


if __name__ == "__main__":
    unittest.main()
