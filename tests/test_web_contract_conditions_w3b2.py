from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.domain import (
    ContractConditions, ControlledOption, PaymentTermOption, ReviewBlockId,
    TemplateDefaults, TemplateOptionCatalogs, TemplateValidationMetadata,
    TemplateVersionStatus,
)
from icp_renov_contracts.ui.web_host import UiBridge

from test_foundation import scratch


class WebContractConditionsW3B2Tests(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.contracts = self.context.contracts; self.catalog = self.context.template_catalog
        self.contract = self.contracts.create_draft()
        self.legacy_actions = []; self.bridge = UiBridge(self.context, self.legacy_actions.append)
        self.states = []; self.bridge.stateChanged.connect(self.states.append)
        self.bridge.openContract(self.contract.id)
        family = self.catalog.create_template("Entretien B2")
        self.version = self.catalog.create_version(
            family, "B2-1", TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            TemplateValidationMetadata(),
            TemplateDefaults(included_area="Rhône", business_hours="8h–18h", travel_included=True),
            self.catalogs(),
        )
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})

    def tearDown(self):
        self._scratch.__exit__(None, None, None)

    @staticmethod
    def catalogs():
        return TemplateOptionCatalogs(
            vat_rates=("5.5", "20"),
            payment_terms=(
                PaymentTermOption("DUE", "À échéance", True, False),
                PaymentTermOption("CUSTOM", "Modalité personnalisée", False, True),
            ),
            payment_methods=(
                ControlledOption("TRANSFER", "Virement"),
                ControlledOption("CHEQUE", "Chèque"),
            ),
        )

    @staticmethod
    def period(**changes):
        values = {
            "issue_date": None, "start_date": None, "initial_duration_mode": None,
            "initial_duration_months": None, "initial_end_date": None, "signature_city": "",
        }
        values.update(changes); return values

    @staticmethod
    def intervention(**changes):
        values = {
            "included_area": "", "business_hours": "", "travel_included": None,
            "missed_appointment_fee": None, "additional_exclusions": "",
        }
        values.update(changes); return values

    @staticmethod
    def pricing(**changes):
        values = {
            "annual_ht": None, "vat_rate": None, "payment_terms_code": None,
            "payment_due_days": None, "payment_terms_custom_text": "", "payment_methods": [],
        }
        values.update(changes); return values

    def block(self, block_id):
        return next(item for item in self.context.review.review(self.contract.id).blocks if item.id is block_id)

    def test_incomplete_period_and_exact_issue_date_persist_without_signature_date(self):
        result = self.bridge.updateContractPeriod(
            self.contract.id, self.period(issue_date="2026-08-20")
        )
        self.assertTrue(result["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.issue_date, "2026-08-20")
        self.assertIsNone(saved.start_date); self.assertEqual(saved.signature_city, "")
        self.assertNotIn("signature_date", ContractConditions.__dataclass_fields__)
        messages = [issue.message for issue in self.block(ReviewBlockId.PERIOD).issues]
        self.assertIn("Renseignez la date de prise d’effet.", messages)

    def test_standard_end_is_calculated_and_recalculates_without_independent_truth(self):
        first = self.bridge.updateContractPeriod(self.contract.id, self.period(
            issue_date="2026-08-20", start_date="2026-09-01",
            initial_duration_mode="STANDARD", initial_duration_months=12,
            initial_end_date="2099-01-01", signature_city="Lyon",
        ))
        self.assertTrue(first["ok"]); self.assertEqual(first["resolved_end_date"], "2027-08-31")
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertIsNone(saved.initial_end_date); self.assertEqual(saved.initial_duration_months, 12)
        second = self.bridge.updateContractPeriod(self.contract.id, self.period(
            issue_date="2026-08-20", start_date="2026-10-15",
            initial_duration_mode="STANDARD", initial_duration_months=6,
            signature_city="Lyon",
        ))
        self.assertTrue(second["ok"]); self.assertEqual(second["resolved_end_date"], "2027-04-14")
        self.assertEqual(self.contracts.get_conditions(self.contract.id).resolved_end_date, "2027-04-14")

    def test_custom_end_is_explicit_months_clear_and_invalid_coherence_is_safe(self):
        accepted = self.bridge.updateContractPeriod(self.contract.id, self.period(
            issue_date="2026-08-20", start_date="2026-09-01",
            initial_duration_mode="CUSTOM", initial_duration_months=99,
            initial_end_date="2027-06-30",
        ))
        self.assertTrue(accepted["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertIsNone(saved.initial_duration_months); self.assertEqual(saved.initial_end_date, "2027-06-30")
        before = saved
        rejected = self.bridge.updateContractPeriod(self.contract.id, self.period(
            issue_date="2026-08-20", start_date="2027-01-01",
            initial_duration_mode="CUSTOM", initial_end_date="2026-12-31",
        ))
        self.assertFalse(rejected["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id), before)

    def test_intervention_defaults_copy_once_and_user_values_are_plain_contract_data(self):
        copied = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(
            (copied.included_area, copied.business_hours, copied.travel_included),
            ("Rhône", "8h–18h", True),
        )
        text = "Hors nacelle\n**texte brut** <b>sans HTML</b>"
        result = self.bridge.updateContractInterventionConditions(self.contract.id, self.intervention(
            included_area="Métropole de Lyon", business_hours="9h–17h", travel_included=False,
            additional_exclusions=text,
        ))
        self.assertTrue(result["ok"])
        family = self.catalog.create_template("Autre B2")
        later = self.catalog.create_version(
            family, "B2-2", TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            defaults=TemplateDefaults(included_area="France", business_hours="24h/24", travel_included=True),
            catalogs=self.catalogs(),
        )
        self.bridge.selectContractTemplateVersion(self.contract.id, later.id)
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(
            (saved.included_area, saved.business_hours, saved.travel_included, saved.additional_exclusions),
            ("Métropole de Lyon", "9h–17h", False, text),
        )
        self.assertEqual(self.block(ReviewBlockId.INTERVENTION).state.value, "VALID")

    def test_missed_appointment_optional_amount_money_and_travel_control(self):
        none = self.bridge.updateContractInterventionConditions(self.contract.id, self.intervention(
            included_area="Rhône", business_hours="8h–18h", travel_included=True,
        ))
        self.assertTrue(none["ok"]); self.assertIsNone(self.contracts.get_conditions(self.contract.id).missed_appointment_fee)
        amount = self.bridge.updateContractInterventionConditions(self.contract.id, self.intervention(
            included_area="Rhône", business_hours="8h–18h", travel_included=False,
            missed_appointment_fee="45,50",
        ))
        self.assertTrue(amount["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id).missed_appointment_fee, "45.50")
        self.assertEqual(self.bridge.contract_workspace_snapshot()["conditions_b2"]["missed_appointment_fee"], "45,50")
        for bad in (
            self.intervention(included_area="Rhône", business_hours="8h", travel_included="yes"),
            self.intervention(included_area="Rhône", business_hours="8h", travel_included=True, missed_appointment_fee="-1"),
        ):
            self.assertFalse(self.bridge.updateContractInterventionConditions(self.contract.id, bad)["ok"])
        empty = self.contracts.save_conditions(self.contract.id, ContractConditions())
        issues = [item.message for item in self.block(ReviewBlockId.INTERVENTION).issues]
        self.assertTrue(any("zone géographique" in item for item in issues))
        self.assertTrue(any("horaires" in item for item in issues))
        self.assertTrue(any("déplacements" in item for item in issues))

    def test_annual_ht_vat_ttc_use_two_authoritative_inputs_and_admitted_rates(self):
        self.assertIsNone(self.contracts.get_conditions(self.contract.id).vat_rate)
        first = self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="100", vat_rate="20", payment_terms_code="DUE",
            payment_due_days=30, payment_methods=["TRANSFER"],
        ))
        self.assertTrue(first["ok"]); self.assertEqual(first["vat_amount"], "20,00"); self.assertEqual(first["annual_ttc"], "120,00")
        second = self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="100", vat_rate="5.5", payment_terms_code="DUE",
            payment_due_days=30, payment_methods=["TRANSFER"],
        ))
        self.assertTrue(second["ok"]); self.assertEqual(second["vat_amount"], "5,50"); self.assertEqual(second["annual_ttc"], "105,50")
        self.assertEqual(self.bridge.contract_workspace_snapshot()["conditions_b2"]["annual_ht"], "100,00")
        before = self.contracts.get_conditions(self.contract.id)
        self.assertFalse(self.bridge.updateContractPricing(
            self.contract.id, self.pricing(annual_ht="100", vat_rate="10")
        )["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id), before)
        for derived in ("vat_amount", "annual_ttc"):
            self.assertNotIn(derived, ContractConditions.__dataclass_fields__)
            self.assertFalse(self.bridge.updateContractPricing(
                self.contract.id, {**self.pricing(), derived: "999"}
            )["ok"])

    def test_payment_catalog_controls_conditional_fields_methods_and_readiness(self):
        catalog = self.bridge.contract_workspace_snapshot()["conditions_b2"]
        self.assertEqual(
            catalog["payment_terms"],
            [
                {"id": "DUE", "label": "À échéance", "requires_day_count": True, "allows_custom_text": False},
                {"id": "CUSTOM", "label": "Modalité personnalisée", "requires_day_count": False, "allows_custom_text": True},
            ],
        )
        self.assertEqual(
            catalog["payment_method_options"],
            [{"id": "TRANSFER", "label": "Virement"}, {"id": "CHEQUE", "label": "Chèque"}],
        )
        due = self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="100", vat_rate="20", payment_terms_code="DUE",
            payment_due_days=30, payment_terms_custom_text="ignored", payment_methods=["TRANSFER"],
        ))
        self.assertTrue(due["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.payment_due_days, 30); self.assertEqual(saved.payment_terms_custom_text, "")
        custom = self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="100", vat_rate="20", payment_terms_code="CUSTOM",
            payment_due_days=99, payment_terms_custom_text="À réception du rapport", payment_methods=["CHEQUE"],
        ))
        self.assertTrue(custom["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertIsNone(saved.payment_due_days); self.assertEqual(saved.payment_terms_custom_text, "À réception du rapport")
        for bad in (
            self.pricing(payment_terms_code="UNKNOWN"),
            self.pricing(payment_methods=["CASH"]),
        ):
            self.assertFalse(self.bridge.updateContractPricing(self.contract.id, bad)["ok"])
        incomplete = self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="100", vat_rate="20", payment_terms_code="DUE", payment_methods=[],
        ))
        self.assertTrue(incomplete["ok"])
        issues = [item.message for item in self.block(ReviewBlockId.PRICE_PAYMENT).issues]
        self.assertTrue(any("délai de paiement" in item for item in issues))
        self.assertTrue(any("au moins un moyen" in item for item in issues))

    def test_frontend_payment_conditionals_follow_only_catalog_flags(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        engine = QJSEngine()
        engine.evaluate("function esc(value) { return String(value ?? ''); }")
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function paymentConditionalMarkup")
        end = js.index("function syncPaymentTermFields", start)
        evaluated = engine.evaluate(js[start:end])
        self.assertFalse(evaluated.isError(), evaluated.toString())
        payment_terms = self.bridge.contract_workspace_snapshot()["conditions_b2"]["payment_terms"]
        engine.evaluate("contractWorkspaceState = " + json.dumps({"conditions_b2": {"payment_terms": payment_terms}}))

        due_markup = engine.evaluate("paymentConditionalMarkup('DUE', 30, 'texte périmé')").toString()
        self.assertIn('id="contract-payment-due-days"', due_markup)
        self.assertNotIn('id="contract-payment-custom"', due_markup)
        self.assertNotIn("texte périmé", due_markup)

        custom_markup = engine.evaluate("paymentConditionalMarkup('CUSTOM', 99, 'À réception du rapport')").toString()
        self.assertNotIn('id="contract-payment-due-days"', custom_markup)
        self.assertIn('id="contract-payment-custom"', custom_markup)
        self.assertIn("À réception du rapport", custom_markup)

        self.assertEqual(engine.evaluate("paymentConditionalMarkup('UNKNOWN', 99, 'libre')").toString(), "")

    def test_period_intervention_pricing_survive_template_change_without_live_binding(self):
        self.bridge.updateContractPeriod(self.contract.id, self.period(
            issue_date="2026-08-20", start_date="2026-09-01", initial_duration_mode="STANDARD",
            initial_duration_months=12, signature_city="Lyon",
        ))
        self.bridge.updateContractInterventionConditions(self.contract.id, self.intervention(
            included_area="Lyon", business_hours="9h–17h", travel_included=True,
        ))
        self.bridge.updateContractPricing(self.contract.id, self.pricing(
            annual_ht="250", vat_rate="20", payment_terms_code="DUE",
            payment_due_days=30, payment_methods=["TRANSFER"],
        ))
        before = self.contracts.get_conditions(self.contract.id)
        family = self.catalog.create_template("Nouveaux défauts")
        other = self.catalog.create_version(
            family, "2", TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            defaults=TemplateDefaults(included_area="Autre", business_hours="Autres horaires", travel_included=False),
            catalogs=self.catalogs(),
        )
        self.bridge.selectContractTemplateVersion(self.contract.id, other.id)
        after = self.contracts.get_conditions(self.contract.id)
        for field in (
            "issue_date", "start_date", "initial_duration_mode", "initial_duration_months",
            "signature_city", "included_area", "business_hours", "travel_included",
            "annual_ht", "vat_rate", "payment_terms_code", "payment_due_days", "payment_methods",
        ):
            self.assertEqual(getattr(after, field), getattr(before, field), field)

    def test_failure_locked_authority_and_frontend_boundaries(self):
        valid = self.period(issue_date="2026-08-20")
        before = self.contracts.get_conditions(self.contract.id); emissions = len(self.states)
        with mock.patch.object(
            self.contracts.conditions_repository, "save", side_effect=sqlite3.OperationalError("disk")
        ):
            result = self.bridge.updateContractPeriod(self.contract.id, valid)
        self.assertFalse(result["ok"]); self.assertEqual(self.contracts.get_conditions(self.contract.id), before)
        self.assertEqual(len(self.states), emissions)
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contracts SET lifecycle_status='ACTIVE' WHERE id=?", (self.contract.id,))
        self.assertFalse(self.bridge.updateContractPeriod(self.contract.id, valid)["ok"])
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts"
        js = (root / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        bridge = (root / "ui" / "web_host.py").read_text(encoding="utf-8")
        for intent in (
            "updateContractPeriod", "updateContractInterventionConditions", "updateContractPricing",
        ):
            self.assertIn(f"def {intent}", bridge)
        for text in (
            "Calculée automatiquement", "syncPeriodMode", "syncMissedAppointmentFee",
            "syncPaymentTermFields", "Montant TVA", "Total TTC",
        ):
            self.assertIn(text, js)
        b2_render = js[js.index("function renderContractB2"):js.index("function renewalDependentMarkup")]
        for forbidden in (
            "renewal_price_rule", "INDEXED", "Renouvellement", "Fin anticipée", "Conditions particulières",
            "Revue", "Documents & suivi",
        ):
            self.assertNotIn(forbidden, b2_render)
        for forbidden in ("patchContract", "updateContractField", "signature_date", "vat_amount: document"):
            self.assertNotIn(forbidden, bridge + js)


if __name__ == "__main__":
    unittest.main()
