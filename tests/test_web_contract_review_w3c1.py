from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import (
    ContractConditions, ReviewBlockId, TemplateValidationMetadata, TemplateVersionStatus,
)
from icp_renov_contracts.ui.web_host import UiBridge

from test_review import FakeCapabilities, ReviewCase


FROZEN_TITLES = [
    "Client & signataire", "Site & équipements", "Cadre du contrat",
    "Modèle & prestations", "Période", "Conditions d’intervention",
    "Prix & paiement", "Renouvellement & fin du contrat", "Conditions particulières",
]


class WebContractReviewW3C1Tests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.context.review.capability_probe = FakeCapabilities(True, True)
        self.context.review.generation_ready = lambda _contract_id: True
        self.states = []
        self.bridge = UiBridge(self.context, lambda _destination: None)
        self.bridge.stateChanged.connect(self.states.append)
        self.bridge.openContract(self.contract.id)

    def snapshot(self):
        return self.bridge.contract_workspace_snapshot()["review"]

    def block(self, result, block_id):
        if isinstance(result, dict):
            return next(item for item in result["blocks"] if item["id"] == block_id.value)
        return super().block(result, block_id)

    def save(self, **changes):
        values = asdict(self.contracts.get_conditions(self.contract.id)); values.update(changes)
        self.contracts.save_conditions(self.contract.id, ContractConditions(**values))

    def event_count(self):
        with self.context.database.connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM contract_events").fetchone()[0]

    def test_exact_nine_block_projection_order_and_complete_state(self):
        self.complete()
        review = self.snapshot()
        self.assertEqual(len(review["blocks"]), 9)
        self.assertEqual([item["title"] for item in review["blocks"]], FROZEN_TITLES)
        self.assertTrue(review["data_complete"])
        self.assertTrue(all(item["state"] == "VALID" for item in review["blocks"]))
        self.assertTrue(review["generation_available"])

    def test_incomplete_zero_equipment_missing_regime_and_defensive_model_state(self):
        empty = self.snapshot()
        self.assertFalse(empty["data_complete"])
        self.assertIn("Choisissez le régime du contrat.", self.block(empty, ReviewBlockId.CONTRACT_CONTEXT)["issues"])
        self.assertIn("Aucun équipement sélectionné.", self.block(empty, ReviewBlockId.SITE_EQUIPMENT)["issues"])
        self.assertNotIn("CONSUMER", json.dumps(empty, ensure_ascii=False))

        *_, version = self.complete()
        self.catalog.update_status(version.id, TemplateVersionStatus.ARCHIVED)
        unavailable = self.snapshot()
        self.assertIn(
            "Le modèle sélectionné n’est plus disponible pour ce contrat.",
            self.block(unavailable, ReviewBlockId.MODEL_SERVICES)["issues"],
        )

    def test_context_period_and_authoritative_pricing_summaries(self):
        self.complete()
        review = self.snapshot()
        context = self.block(review, ReviewBlockId.CONTRACT_CONTEXT)["summary"]
        self.assertEqual(context, "Consommateur")
        self.assertNotIn("Conclusion", context)
        js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        self.assertIn("!contractWorkspaceState?.conditions_b1?.conclusion_required", js)
        period = self.block(review, ReviewBlockId.PERIOD)["summary"]
        self.assertIn("Durée standard · 12 mois", period)
        price = self.block(review, ReviewBlockId.PRICE_PAYMENT)["summary"]
        for expected in ("100,00 € HT", "TVA 20 % (20,00 €)", "120,00 € TTC", "À échéance", "30 jours", "Virement"):
            self.assertIn(expected, price)

        self.save(initial_duration_mode="CUSTOM", initial_duration_months=None, initial_end_date="2027-03-15")
        self.assertIn("Date de fin personnalisée", self.block(self.snapshot(), ReviewBlockId.PERIOD)["summary"])

    def test_payment_conditionals_and_special_terms_are_summary_only(self):
        self.complete()
        self.save(payment_terms_code="CUSTOM", payment_due_days=None, payment_terms_custom_text="À réception")
        price = self.block(self.snapshot(), ReviewBlockId.PRICE_PAYMENT)["summary"]
        self.assertIn("Personnalisé", price); self.assertIn("À réception", price)
        self.assertNotIn("30 jours", price)
        special = self.block(self.snapshot(), ReviewBlockId.SPECIAL_TERMS)
        self.assertEqual(special["summary"], "Aucune condition particulière")
        self.save(special_terms="<b>Texte</b>\n**brut**")
        special = self.block(self.snapshot(), ReviewBlockId.SPECIAL_TERMS)
        self.assertEqual(special["summary"], "Conditions particulières renseignées")
        self.assertNotIn("<b>", json.dumps(special, ensure_ascii=False))

    def test_none_manual_and_tacit_required_optional_review_summaries(self):
        self.complete(renewal="NONE")
        none = self.block(self.snapshot(), ReviewBlockId.RENEWAL_END)["summary"]
        self.assertEqual(none, "Aucun renouvellement")

        self.save(renewal_mode="MANUAL", renewal_period_months=12, renewal_price_rule="FIXED", internal_alert_days=90)
        manual = self.block(self.snapshot(), ReviewBlockId.RENEWAL_END)["summary"]
        for expected in ("Renouvellement manuel", "Période de 12 mois", "Même prix", "Alerte interne 90 jours avant"):
            self.assertIn(expected, manual)

        self.save(renewal_mode="TACIT", non_renewal_notice_days=60, non_renewal_notice_channels=("EMAIL",))
        required = self.block(self.snapshot(), ReviewBlockId.RENEWAL_END)["summary"]
        self.assertIn("Préavis de non-renouvellement : 60 jours", required)
        self.assertIn("Canaux : E-mail", required)
        self.assertNotIn("INDEXED", required)

        optional_version = self.create_model(TemplateValidationMetadata())
        self.contracts.select_template_version(self.contract.id, optional_version.id)
        optional = self.block(self.snapshot(), ReviewBlockId.RENEWAL_END)["summary"]
        self.assertNotIn("Préavis de non-renouvellement", optional)
        self.assertNotIn("Canaux :", optional)

    def test_business_completeness_is_distinct_from_generation_availability(self):
        self.complete()
        self.context.review.capability_probe = FakeCapabilities(True, False, "Conversion PDF indisponible")
        review = self.snapshot()
        self.assertTrue(review["data_complete"])
        self.assertFalse(review["generation_available"])
        self.assertEqual([item["key"] for item in review["generation_checks"]], ["MODEL", "WORKSPACE", "DOCX", "PDF"])
        self.assertFalse(review["generation_checks"][-1]["available"])

    def test_step_three_and_all_modifier_routes_are_closed_and_exact(self):
        self.complete()
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 3)["ok"])
        self.assertEqual(self.bridge.contract_workspace_snapshot()["active_step"], 3)
        expected = {
            "CLIENT_SIGNATORY": (1, "client-signatory"), "SITE_EQUIPMENT": (1, "site-equipment"),
            "CONTRACT_CONTEXT": (2, "contract-context"), "MODEL_SERVICES": (2, "model-services"),
            "PERIOD": (2, "period"), "INTERVENTION": (2, "intervention"),
            "PRICE_PAYMENT": (2, "price-payment"), "RENEWAL_END": (2, "renewal-end"),
            "SPECIAL_TERMS": (2, "special-terms"),
        }
        for block_id, (step, target) in expected.items():
            with self.subTest(block=block_id):
                result = self.bridge.openContractReviewBlock(self.contract.id, block_id)
                self.assertEqual((result["step"], result["target"]), (step, target))
        self.assertFalse(self.bridge.openContractReviewBlock(self.contract.id, "UNKNOWN")["ok"])
        # D4 intentionally opens Step 4 for an ungenerated DRAFT so it can be
        # abandoned without inventing a revision.
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        self.assertTrue(self.bridge.contract_workspace_snapshot()["documents_d4"]["abandon_allowed"])

    def test_opening_review_is_read_only_and_frontend_action_is_disabled(self):
        self.complete()
        before_events = self.event_count()
        before_contract = self.contracts.get(self.contract.id)
        before_conditions = self.contracts.get_conditions(self.contract.id)
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 3)["ok"])
        self.bridge.contract_workspace_snapshot()
        self.assertEqual(self.event_count(), before_events)
        self.assertEqual(self.contracts.get(self.contract.id), before_contract)
        self.assertEqual(self.contracts.get_conditions(self.contract.id), before_conditions)

        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renderContractReview")
        end = js.index("function renderContractConditions", start)
        engine = QJSEngine()
        engine.evaluate("function esc(value){return String(value ?? '');} var contractSaveFailure=''; var contractGenerationRunning=false;")
        evaluated = engine.evaluate(js[start:end]); self.assertFalse(evaluated.isError(), evaluated.toString())
        rendered = engine.evaluate("renderContractReview(" + json.dumps(self.bridge.contract_workspace_snapshot()) + ")")
        self.assertFalse(rendered.isError(), rendered.toString())
        html = rendered.toString()
        self.assertEqual(html.count('class=\"review-block valid\"'), 9)
        self.assertEqual(html.count('>Modifier</button>'), 9)
        self.assertIn('class=\"primary review-generation-action\" disabled', html)
        css = (root / "contract-workspace-ds01d.css").read_text(encoding="utf-8")
        for selector in (
            ".review-business-state.valid", ".review-business-state.error",
            ".review-block.valid", ".review-block.error",
            ".review-generation-action.is-busy",
        ):
            self.assertIn(selector, css)
        for obsolete_selector in (".review-block.complete", ".review-block.warning", ".review-block.blocking"):
            self.assertNotIn(obsolete_selector, css)
        engine.evaluate("contractGenerationRunning=true;")
        busy = engine.evaluate("renderContractReview(" + json.dumps(self.bridge.contract_workspace_snapshot()) + ")").toString()
        self.assertIn('class=\"primary review-generation-action is-busy\" disabled aria-busy=\"true\"', busy)
        self.assertIn("Génération en cours…", busy)
        for forbidden in ("INDEXED", "placeholder", "source_hash", "R01", "TO_SIGN"):
            self.assertNotIn(forbidden, html)


if __name__ == "__main__":
    import unittest
    unittest.main()
