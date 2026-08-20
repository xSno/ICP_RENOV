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
    ContractConditions, ControlledOption, ReviewBlockId, TemplateDefaults,
    TemplateOptionCatalogs, TemplateValidationMetadata, TemplateVersionStatus,
)
from icp_renov_contracts.ui.web_host import UiBridge

from test_foundation import scratch


class WebContractConditionsW3B3Tests(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.contracts = self.context.contracts; self.catalog = self.context.template_catalog
        self.contract = self.contracts.create_draft()
        self.states = []; self.bridge = UiBridge(self.context, lambda _action: None)
        self.bridge.stateChanged.connect(self.states.append); self.bridge.openContract(self.contract.id)
        self.family = self.catalog.create_template("Entretien B3")
        self.version = self.catalog.create_version(
            self.family, "B3-1", TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            TemplateValidationMetadata(
                requires_non_renewal_notice_days=True,
                requires_non_renewal_notice_channels=True,
            ),
            TemplateDefaults(renewal_mode="TACIT", renewal_period_months=12, internal_alert_days=90),
            self.catalogs(),
        )
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})

    def tearDown(self): self._scratch.__exit__(None, None, None)

    @staticmethod
    def catalogs():
        return TemplateOptionCatalogs(
            non_renewal_channels=(
                ControlledOption("EMAIL", "E-mail"), ControlledOption("POST", "Courrier"),
            ),
            early_termination_reasons=(
                ControlledOption("BREACH", "Manquement"), ControlledOption("OTHER", "Autre"),
            ),
        )

    @staticmethod
    def renewal(**changes):
        values = {
            "renewal_mode": None, "renewal_period_months": None,
            "non_renewal_notice_days": None, "non_renewal_notice_channels": [],
            "internal_alert_days": None, "renewal_price_rule": None,
        }
        values.update(changes); return values

    def renewal_block(self):
        return next(
            item for item in self.context.review.review(self.contract.id).blocks
            if item.id is ReviewBlockId.RENEWAL_END
        )

    def governed_version(self, name, *, notice_days=False, notice_channels=False, cure_days=False):
        return self.catalog.create_version(
            self.family, name, TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            TemplateValidationMetadata(
                requires_non_renewal_notice_days=notice_days,
                requires_non_renewal_notice_channels=notice_channels,
                requires_breach_cure_period_days=cure_days,
            ),
            catalogs=self.catalogs(),
        )

    def test_snapshot_projects_only_existing_controlled_vocabularies(self):
        b3 = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        self.assertEqual([item["id"] for item in b3["renewal_modes"]], ["NONE", "MANUAL", "TACIT"])
        self.assertEqual(
            [item["id"] for item in b3["renewal_price_rules"]],
            ["FIXED", "NEW_PRICE_ON_RENEWAL"],
        )
        self.assertEqual([item["id"] for item in b3["non_renewal_channel_options"]], ["EMAIL", "POST"])
        self.assertEqual([item["id"] for item in b3["early_termination_reason_options"]], ["BREACH", "OTHER"])
        self.assertTrue(b3["requires_non_renewal_notice_days"])
        self.assertTrue(b3["requires_non_renewal_notice_channels"])
        self.assertFalse(b3["breach_cure_period_required"])
        self.assertIsNone(b3["breach_cure_period_days"])

    def test_requiredness_metadata_roundtrips_and_old_json_defaults_false(self):
        old = TemplateValidationMetadata.from_json(
            '{"conclusion_required_regimes":[],"context_authorizations":[]}'
        )
        self.assertFalse(old.requires_non_renewal_notice_days)
        self.assertFalse(old.requires_non_renewal_notice_channels)
        self.assertFalse(old.requires_breach_cure_period_days)
        expected = TemplateValidationMetadata(
            requires_non_renewal_notice_days=True,
            requires_non_renewal_notice_channels=False,
            requires_breach_cure_period_days=True,
        )
        self.assertEqual(TemplateValidationMetadata.from_json(expected.to_json()), expected)

    def test_version_metadata_independently_controls_tacit_ui_review_and_stale_cleanup(self):
        missing = [issue.message for issue in self.renewal_block().issues]
        self.assertTrue(any("préavis" in item for item in missing))
        self.assertTrue(any("canal" in item for item in missing))
        self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="TACIT", renewal_period_months=12,
            non_renewal_notice_days=60, non_renewal_notice_channels=["EMAIL"],
            internal_alert_days=90, renewal_price_rule="FIXED",
        ))

        optional = self.governed_version("B3-optional")
        self.assertTrue(self.bridge.selectContractTemplateVersion(self.contract.id, optional.id)["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertIsNone(saved.non_renewal_notice_days)
        self.assertEqual(saved.non_renewal_notice_channels, ())
        snapshot = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        self.assertFalse(snapshot["requires_non_renewal_notice_days"])
        self.assertFalse(snapshot["requires_non_renewal_notice_channels"])
        optional_issues = [issue.message for issue in self.renewal_block().issues]
        self.assertFalse(any("préavis" in item or "canal" in item for item in optional_issues))

        days_only = self.governed_version("B3-days", notice_days=True)
        self.bridge.selectContractTemplateVersion(self.contract.id, days_only.id)
        snapshot = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        self.assertTrue(snapshot["requires_non_renewal_notice_days"])
        self.assertFalse(snapshot["requires_non_renewal_notice_channels"])
        days_issues = [issue.message for issue in self.renewal_block().issues]
        self.assertTrue(any("préavis" in item for item in days_issues))
        self.assertFalse(any("canal" in item for item in days_issues))

        channels_only = self.governed_version("B3-channels", notice_channels=True)
        self.bridge.selectContractTemplateVersion(self.contract.id, channels_only.id)
        snapshot = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        self.assertFalse(snapshot["requires_non_renewal_notice_days"])
        self.assertTrue(snapshot["requires_non_renewal_notice_channels"])
        channel_issues = [issue.message for issue in self.renewal_block().issues]
        self.assertFalse(any("préavis" in item for item in channel_issues))
        self.assertTrue(any("canal" in item for item in channel_issues))

    def test_cure_period_is_version_governed_and_service_cleaned(self):
        governed = self.governed_version("B3-cure", cure_days=True)
        self.bridge.selectContractTemplateVersion(self.contract.id, governed.id)
        snapshot = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        self.assertTrue(snapshot["breach_cure_period_required"])
        self.assertTrue(any("régularisation" in issue.message for issue in self.renewal_block().issues))
        saved = self.bridge.updateContractEarlyTermination(self.contract.id, {
            "early_termination_reason_codes": ["BREACH"],
            "early_termination_custom_text": "",
            "breach_cure_period_days": 15,
        })
        self.assertTrue(saved["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id).breach_cure_period_days, 15)
        self.assertFalse(any("régularisation" in issue.message for issue in self.renewal_block().issues))
        optional = self.governed_version("B3-no-cure")
        self.bridge.selectContractTemplateVersion(self.contract.id, optional.id)
        self.assertIsNone(self.contracts.get_conditions(self.contract.id).breach_cure_period_days)

    def test_none_clears_every_hidden_renewal_truth_and_has_no_dependent_markup(self):
        result = self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="NONE", renewal_period_months=12, non_renewal_notice_days=60,
            non_renewal_notice_channels=["EMAIL"], internal_alert_days=90,
            renewal_price_rule="FIXED",
        ))
        self.assertTrue(result["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.renewal_mode, "NONE")
        self.assertEqual(
            (saved.renewal_period_months, saved.non_renewal_notice_days,
             saved.non_renewal_notice_channels, saved.internal_alert_days, saved.renewal_price_rule),
            (None, None, (), None, None),
        )
        self.assertEqual(self.bridge.contract_workspace_snapshot()["summary"]["renewal"], "Aucun")

    def test_manual_keeps_common_fields_and_clears_tacit_notice(self):
        result = self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="MANUAL", renewal_period_months=12,
            non_renewal_notice_days=60, non_renewal_notice_channels=["EMAIL"],
            internal_alert_days=90, renewal_price_rule="FIXED",
        ))
        self.assertTrue(result["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(
            (saved.renewal_period_months, saved.internal_alert_days, saved.renewal_price_rule),
            (12, 90, "FIXED"),
        )
        self.assertIsNone(saved.non_renewal_notice_days); self.assertEqual(saved.non_renewal_notice_channels, ())
        self.assertEqual(self.bridge.contract_workspace_snapshot()["summary"]["renewal"], "Renouvellement manuel · Même prix")

    def test_tacit_keeps_contractual_notice_distinct_from_internal_alert(self):
        result = self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="TACIT", renewal_period_months=12,
            non_renewal_notice_days=60, non_renewal_notice_channels=["EMAIL", "POST"],
            internal_alert_days=90, renewal_price_rule="FIXED",
        ))
        self.assertTrue(result["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.non_renewal_notice_days, 60)
        self.assertEqual(saved.non_renewal_notice_channels, ("EMAIL", "POST"))
        self.assertEqual(saved.internal_alert_days, 90)
        self.assertNotEqual(saved.non_renewal_notice_days, saved.internal_alert_days)
        rejected = self.bridge.updateContractRenewal(
            self.contract.id, self.renewal(renewal_mode="TACIT", non_renewal_notice_channels=["SMS"])
        )
        self.assertFalse(rejected["ok"]); self.assertEqual(self.contracts.get_conditions(self.contract.id), saved)

    def test_renewal_requiredness_price_rules_and_no_future_price_truth(self):
        incomplete = self.bridge.updateContractRenewal(
            self.contract.id, self.renewal(renewal_mode="MANUAL")
        )
        self.assertTrue(incomplete["ok"])
        messages = [issue.message for issue in self.renewal_block().issues]
        self.assertTrue(any("durée de renouvellement" in item for item in messages))
        self.assertTrue(any("règle de prix" in item for item in messages))
        for rule in ("FIXED", "NEW_PRICE_ON_RENEWAL"):
            with self.subTest(rule=rule):
                accepted = self.bridge.updateContractRenewal(self.contract.id, self.renewal(
                    renewal_mode="MANUAL", renewal_period_months=12,
                    internal_alert_days=90, renewal_price_rule=rule,
                ))
                self.assertTrue(accepted["ok"])
        before = self.contracts.get_conditions(self.contract.id)
        self.assertFalse(self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="MANUAL", renewal_period_months=12,
            internal_alert_days=90, renewal_price_rule="INDEXED",
        ))["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id), before)
        self.assertFalse(self.bridge.updateContractRenewal(
            self.contract.id, {**self.renewal(), "future_annual_ht": "999"}
        )["ok"])

    def test_renewal_defaults_copy_once_without_live_binding(self):
        copied = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(
            (copied.renewal_mode, copied.renewal_period_months, copied.internal_alert_days),
            ("TACIT", 12, 90),
        )
        self.bridge.updateContractRenewal(self.contract.id, self.renewal(
            renewal_mode="MANUAL", renewal_period_months=18,
            internal_alert_days=75, renewal_price_rule="FIXED",
        ))
        family = self.catalog.create_template("Autres valeurs B3")
        later = self.catalog.create_version(
            family, "B3-2", TemplateVersionStatus.AVAILABLE, ("CONSUMER",),
            defaults=TemplateDefaults(renewal_mode="NONE", renewal_period_months=24, internal_alert_days=30),
            catalogs=self.catalogs(),
        )
        self.bridge.selectContractTemplateVersion(self.contract.id, later.id)
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(
            (saved.renewal_mode, saved.renewal_period_months, saved.internal_alert_days),
            ("MANUAL", 18, 75),
        )

    def test_frontend_none_manual_tacit_markup_is_genuinely_conditional(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renewalDependentMarkup")
        end = js.index("function syncRenewalFields", start)
        engine = QJSEngine(); engine.evaluate("function esc(value) { return String(value ?? ''); }")
        evaluated = engine.evaluate(js[start:end]); self.assertFalse(evaluated.isError(), evaluated.toString())
        b3 = self.bridge.contract_workspace_snapshot()["conditions_b3"]
        engine.evaluate("contractWorkspaceState = " + json.dumps({"conditions_b3": b3}))
        values = json.dumps({"period": 12, "noticeDays": 60, "channels": ["EMAIL"], "alertDays": 90, "priceRule": "FIXED"})
        none = engine.evaluate(f"renewalDependentMarkup('NONE',{values})").toString()
        manual = engine.evaluate(f"renewalDependentMarkup('MANUAL',{values})").toString()
        tacit = engine.evaluate(f"renewalDependentMarkup('TACIT',{values})").toString()
        self.assertEqual(none, "")
        for field in ("contract-renewal-period", "contract-renewal-price-rule", "contract-internal-alert-days"):
            self.assertIn(field, manual); self.assertIn(field, tacit)
        for field in ("contract-non-renewal-notice-days", "non-renewal-channel"):
            self.assertNotIn(field, manual); self.assertIn(field, tacit)
        self.assertNotIn("INDEXED", none + manual + tacit)

        b3["requires_non_renewal_notice_days"] = False
        b3["requires_non_renewal_notice_channels"] = False
        engine.evaluate("contractWorkspaceState = " + json.dumps({"conditions_b3": b3}))
        optional = engine.evaluate(f"renewalDependentMarkup('TACIT',{values})").toString()
        for field in ("contract-renewal-period", "contract-renewal-price-rule", "contract-internal-alert-days"):
            self.assertIn(field, optional)
        self.assertNotIn("contract-non-renewal-notice-days", optional)
        self.assertNotIn("non-renewal-channel", optional)

    def test_early_termination_is_controlled_secondary_surface_without_unauthorized_cure_field(self):
        accepted = self.bridge.updateContractEarlyTermination(self.contract.id, {
            "early_termination_reason_codes": ["BREACH"],
            "early_termination_custom_text": "Précision\n**texte brut**",
            "breach_cure_period_days": None,
        })
        self.assertTrue(accepted["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.early_termination_reason_codes, ("BREACH",))
        self.assertEqual(saved.early_termination_custom_text, "Précision\n**texte brut**")
        for payload in (
            {"early_termination_reason_codes": ["UNKNOWN"], "early_termination_custom_text": "", "breach_cure_period_days": None},
            {"early_termination_reason_codes": ["BREACH"], "early_termination_custom_text": "", "breach_cure_period_days": "quinze"},
        ):
            self.assertFalse(self.bridge.updateContractEarlyTermination(self.contract.id, payload)["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id), saved)
        cleared = self.bridge.updateContractEarlyTermination(self.contract.id, {
            "early_termination_reason_codes": ["BREACH"],
            "early_termination_custom_text": "Précision\n**texte brut**",
            "breach_cure_period_days": 15,
        })
        self.assertTrue(cleared["ok"])
        self.assertIsNone(self.contracts.get_conditions(self.contract.id).breach_cure_period_days)
        js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        self.assertIn("Modifier les conditions de fin anticipée", js)
        self.assertIn("role=\"dialog\"", js)
        self.assertIn("contract-breach-cure", js)
        self.assertIn("1 motif sélectionné", js)
        self.assertIn("motifs sélectionnés", js)
        self.assertIn("Aucune précision contractuelle renseignée.", js)
        self.assertNotIn("motif(s) contrôlé(s)", js)
        self.assertNotIn("Aucune précision contractuelle est renseignée.", js)

    def test_special_terms_are_plain_multiline_and_closed(self):
        value = "Ligne 1\n**Markdown non interprété**\n<b>HTML non interprété</b>"
        accepted = self.bridge.updateContractSpecialTerms(self.contract.id, {"special_terms": value})
        self.assertTrue(accepted["ok"])
        self.assertEqual(self.contracts.get_conditions(self.contract.id).special_terms, value)
        self.assertFalse(self.bridge.updateContractSpecialTerms(
            self.contract.id, {"special_terms": value, "html": True}
        )["ok"])
        js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        self.assertIn("${esc(b3.special_terms || '')}", js)
        self.assertNotIn("contenteditable", js)

    def test_locked_failure_and_b3_scope_boundaries(self):
        payload = self.renewal(renewal_mode="NONE")
        before = self.contracts.get_conditions(self.contract.id); emissions = len(self.states)
        with mock.patch.object(
            self.contracts.conditions_repository, "save", side_effect=sqlite3.OperationalError("disk")
        ):
            result = self.bridge.updateContractRenewal(self.contract.id, payload)
        self.assertFalse(result["ok"]); self.assertEqual(self.contracts.get_conditions(self.contract.id), before)
        self.assertEqual(len(self.states), emissions)
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contracts SET lifecycle_status='ACTIVE' WHERE id=?", (self.contract.id,))
        self.assertFalse(self.bridge.updateContractRenewal(self.contract.id, payload)["ok"])
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts"
        js = (root / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        bridge = (root / "ui" / "web_host.py").read_text(encoding="utf-8")
        for intent in ("updateContractRenewal", "updateContractEarlyTermination", "updateContractSpecialTerms"):
            self.assertIn(f"def {intent}", bridge)
        b3 = js[js.index("function renewalDependentMarkup"):js.index("function openReviewBlock")]
        for forbidden in (
            "future_annual_ht", "index reference", "CPI", "INSEE", "Step 3", "Étape 3",
            "generation", "document generation", "termination scheduling",
        ):
            self.assertNotIn(forbidden, b3)


if __name__ == "__main__":
    unittest.main()
