from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import ContractEventType, ContractStatus, DocumentKind, TemplateVersionStatus
from icp_renov_contracts.services import InterventionInput
from icp_renov_contracts.ui.web_host import UiBridge

from test_intervention_s9 import InterventionCase


class WebInterventionW3D5Tests(InterventionCase):
    def setUp(self):
        super().setUp()
        self.context = replace(self.context, intervention_generation=self.sheet_service)
        self.bridge = UiBridge(self.context, lambda _destination: None)
        self.bridge.openContract(self.contract.id)

    def _state(self):
        return self.bridge.contract_workspace_snapshot()

    def test_step_four_offers_only_available_intervention_templates_and_no_model_state_is_bounded(self):
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        d5 = self._state()["documents_d5"]
        self.assertTrue(d5["action_available"])
        self.assertEqual([item["id"] for item in d5["templates"]], [self.sheet_version.id])
        self.assertNotIn(self.version.id, [item["id"] for item in d5["templates"]])
        pending = self.catalog.create_version(self.sheet_template, "PENDING", TemplateVersionStatus.TO_VALIDATE, ())
        self.assertNotIn(pending.id, [item["id"] for item in self._state()["documents_d5"]["templates"]])

    def test_web_generation_persists_an_intervention_document_without_contract_mutation(self):
        before = self.contracts.get(self.contract.id)
        before_revisions = self.context.lifecycle.revisions(self.contract.id)
        payload = {
            "date": "2026-08-14", "technician": "Camille", "other": "Contrôle annuel",
            "notes": "Texte simple", "issues": "Aucune anomalie", "quote_recommended": False,
        }
        result = self.bridge.generateInterventionSheet(self.contract.id, self.sheet_version.id, payload)
        self.assertTrue(result["ok"])
        document = self.context.lifecycle.documents.get(result["document_id"])
        self.assertIs(document.document_kind, DocumentKind.INTERVENTION_SHEET)
        self.assertIsNone(document.revision)
        self.assertEqual((self.contracts.get(self.contract.id).number, self.contracts.get(self.contract.id).status), (before.number, before.status))
        self.assertEqual(self.context.lifecycle.revisions(self.contract.id), before_revisions)
        d5 = self._state()["documents_d5"]
        self.assertEqual(len(d5["documents"]), 1)
        self.assertEqual((d5["documents"][0]["intervention_date_display"], d5["documents"][0]["technician"]), ("14/08/2026", "Camille"))
        self.assertEqual(document.template_version_id, self.sheet_version.id)
        self.assertTrue(any(event.type is ContractEventType.DOCUMENT_GENERATED and event.document_id == document.id for event in self.context.lifecycle.history(self.contract.id)))

    def test_document_generated_history_uses_the_referenced_document_kind(self):
        r01 = self.service().generate(self.contract.id).document
        first = self.bridge.generateInterventionSheet(
            self.contract.id, self.sheet_version.id, {"date": "2026-08-14", "technician": "Camille"}
        )
        second = self.bridge.generateInterventionSheet(
            self.contract.id, self.sheet_version.id, {"date": "2026-08-21", "technician": "Samira"}
        )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

        generated = [
            item for item in self._state()["documents_d1"]["timeline"]
            if item["label"] in {"Révision contractuelle générée", "Fiche d’intervention générée"}
        ]
        intervention_entries = [item for item in generated if item["label"] == "Fiche d’intervention générée"]
        contract_entries = [item for item in generated if item["label"] == "Révision contractuelle générée"]
        self.assertEqual(len(intervention_entries), 2)
        self.assertTrue(all(item["revision"] == "" for item in intervention_entries))
        self.assertEqual([(item["label"], item["revision"]) for item in contract_entries], [
            ("Révision contractuelle générée", r01.revision)
        ])
        self.assertNotIn("INTERVENTION_GENERATED", {event_type.name for event_type in ContractEventType})

    def test_bridge_rejects_contract_template_unknown_document_and_invalid_payload_without_side_effect(self):
        before = self.context.lifecycle.documents.list_for_contract_kind(self.contract.id, DocumentKind.INTERVENTION_SHEET)
        bad_template = self.bridge.generateInterventionSheet(self.contract.id, self.version.id, {"date": "2026-08-14"})
        bad_payload = self.bridge.generateInterventionSheet(self.contract.id, self.sheet_version.id, {"date": 12})
        self.assertFalse(bad_template["ok"])
        self.assertFalse(bad_payload["ok"])
        self.assertEqual(self.context.lifecycle.documents.list_for_contract_kind(self.contract.id, DocumentKind.INTERVENTION_SHEET), before)
        self.assertFalse(self.bridge.openInterventionDocument(self.contract.id, "unknown", "pdf")["ok"])

    def test_web_surface_has_french_date_modal_and_other_document_cards_without_revision_copy(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        path = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js"
        js = path.read_text(encoding="utf-8")
        self.assertIn("openInterventionSheetModal", js)
        self.assertIn("Date de l’intervention", js)
        self.assertIn("JJ/MM/AAAA", js)
        self.assertIn("Anomalies / points signalés", js)
        self.assertIn("Aucun modèle de fiche d’intervention disponible.", js)
        self.assertIn("openInterventionFile", js)
        self.assertNotIn("R01", js[js.index("function openInterventionSheetModal"):js.index("function openCorrectionConfirmation")])
        start = js.index("function renderContractDocuments")
        end = js.index("function renderContractReview", start)
        engine = QJSEngine()
        evaluated = engine.evaluate("function esc(value){return String(value ?? '');} var contractDocumentsError='';" + js[start:end])
        self.assertFalse(evaluated.isError(), evaluated.toString())
        base = {"documents_d1": {"feedback": None, "revisions": [], "timeline": [], "correction_allowed": False, "send_allowed": False}, "documents_d2": {"signature_allowed": False}, "documents_d3": {}, "documents_d4": {"abandon_allowed": False}, "documents_d5": {"action_available": True, "documents": [{"id": "sheet-1", "intervention_date_display": "14/08/2026", "technician": "Camille", "generated_display": "14/08/2026 10:00", "template_name": "Fiche", "template_version": "1", "docx_available": True, "pdf_available": True}]}}
        html = engine.evaluate("renderContractDocuments(" + __import__("json").dumps(base) + ")").toString()
        self.assertIn("Créer une fiche d’intervention", html)
        self.assertIn("Fiche d’intervention", html)
        self.assertIn("14/08/2026", html)
        self.assertNotIn("R00", html)


if __name__ == "__main__":
    unittest.main()
