from __future__ import annotations

import sqlite3
from pathlib import Path
import unittest
from unittest import mock

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.domain import (
    ContextAuthorization, INCLUDED_OPTIONS, TemplateDefaults, TemplateValidationMetadata,
    TemplateVersionStatus,
)
from icp_renov_contracts.ui.web_host import UiBridge

from test_foundation import scratch
from test_master_data import organization


class WebContractConditionsW3B1Tests(unittest.TestCase):
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

    def tearDown(self):
        self._scratch.__exit__(None, None, None)

    def template(self, name="Entretien annuel", *, kind="CONTRACT", type_code="CLIMATE_MAINTENANCE"):
        return self.catalog.create_template(name, kind, type_code)

    def version(self, template, version="1.0", *, status=TemplateVersionStatus.AVAILABLE,
                regimes=("CONSUMER",), validation=None, defaults=None):
        return self.catalog.create_version(
            template, version, status, regimes, validation or TemplateValidationMetadata(),
            defaults or TemplateDefaults(),
        )

    def test_empty_draft_and_regime_authority_are_exact_and_contract_owned(self):
        self.assertIsNone(self.contracts.get(self.contract.id).regime)
        self.assertIsNotNone(self.contracts.get_conditions(self.contract.id))
        before = self.context.master_data.create_client(organization("Ateliers du Rhône"))
        self.contracts.select_client(self.contract.id, before.id)
        snapshot = self.bridge.contract_workspace_snapshot()
        self.assertEqual(
            [item["id"] for item in snapshot["conditions_b1"]["regime_options"]],
            ["CONSUMER", "NON_PROFESSIONAL", "PROFESSIONAL"],
        )
        rejected = self.bridge.updateContractFramework(self.contract.id, {"regime": "OTHER"})
        self.assertFalse(rejected["ok"])
        accepted = self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        self.assertTrue(accepted["ok"])
        self.assertEqual(self.contracts.get(self.contract.id).regime.value, "CONSUMER")
        self.assertEqual(self.context.master_data.get_client(before.id), before)

    def test_filtering_is_contract_available_type_and_exact_regime_only(self):
        family = self.template()
        consumer = self.version(family, "consumer")
        nonprofessional = self.version(family, "nonpro", regimes=("NON_PROFESSIONAL",))
        pending = self.version(family, "pending", status=TemplateVersionStatus.TO_VALIDATE)
        archived = self.version(family, "archived", status=TemplateVersionStatus.ARCHIVED)
        wrong_type = self.template("Autre type", type_code="OTHER")
        wrong_type_version = self.version(wrong_type, "other")
        sheet = self.template("Fiche intervention", kind="INTERVENTION_SHEET")
        sheet_version = self.version(sheet, "sheet", regimes=())
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        ids = [item.id for item in self.contracts.compatible_template_versions(self.contract.id)]
        self.assertEqual(ids, [consumer.id])
        for excluded in (nonprofessional, pending, archived, wrong_type_version, sheet_version):
            self.assertNotIn(excluded.id, ids)
        self.assertEqual(self.contracts.get(self.contract.id).template_version_id, consumer.id)
        self.assertNotIn(nonprofessional.id, ids)
        self.bridge.updateContractFramework(self.contract.id, {"regime": "NON_PROFESSIONAL"})
        self.assertEqual(
            [item.id for item in self.contracts.compatible_template_versions(self.contract.id)],
            [nonprofessional.id],
        )
        self.assertNotIn(consumer.id, [item.id for item in self.contracts.compatible_template_versions(self.contract.id)])

    def test_single_auto_selects_zero_model_is_exact_and_multiple_requires_choice(self):
        family = self.template()
        only = self.version(family, "unique", regimes=("CONSUMER",))
        result = self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        self.assertTrue(result["ok"]); self.assertEqual(result["template_version_id"], only.id)
        self.assertEqual(self.contracts.get(self.contract.id).template_version_id, only.id)
        self.bridge.updateContractFramework(self.contract.id, {"regime": "PROFESSIONAL"})
        state = self.bridge.contract_workspace_snapshot()["conditions_b1"]
        self.assertEqual(state["model_state"], "NO_MODEL")
        self.assertEqual(state["templates"], [])
        self.assertIsNone(self.contracts.get(self.contract.id).template_version_id)
        first = self.version(family, "multi-a", regimes=("NON_PROFESSIONAL",))
        second = self.version(family, "multi-b", regimes=("NON_PROFESSIONAL",))
        self.bridge.updateContractFramework(self.contract.id, {"regime": "NON_PROFESSIONAL"})
        state = self.bridge.contract_workspace_snapshot()["conditions_b1"]
        self.assertEqual({item["id"] for item in state["templates"]}, {first.id, second.id})
        self.assertIsNone(self.contracts.get(self.contract.id).template_version_id)
        self.assertTrue(self.bridge.selectContractTemplateVersion(self.contract.id, second.id)["ok"])

    def test_regime_change_clears_incompatible_and_retains_exact_compatible_version(self):
        family = self.template()
        shared = self.version(family, regimes=("CONSUMER", "NON_PROFESSIONAL"))
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        self.assertEqual(self.contracts.get(self.contract.id).template_version_id, shared.id)
        self.bridge.updateContractFramework(self.contract.id, {"regime": "NON_PROFESSIONAL"})
        self.assertEqual(self.contracts.get(self.contract.id).template_version_id, shared.id)
        self.bridge.updateContractFramework(self.contract.id, {"regime": "PROFESSIONAL"})
        self.assertIsNone(self.contracts.get(self.contract.id).template_version_id)

    def test_conclusion_and_early_performance_follow_only_validated_context(self):
        family = self.template()
        metadata = TemplateValidationMetadata(
            ("CONSUMER",),
            (
                ContextAuthorization("CONSUMER", "OFF_PREMISES", ("BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE")),
                ContextAuthorization("CONSUMER", "DISTANCE_EMAIL", ("BLOCK_WITHDRAWAL",)),
            ),
        )
        self.version(family, validation=metadata)
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        state = self.bridge.contract_workspace_snapshot()["conditions_b1"]
        self.assertTrue(state["conclusion_required"])
        self.assertFalse(state["early_performance_visible"])
        self.assertEqual(
            [item["id"] for item in state["conclusion_options"]],
            ["OFF_PREMISES", "DISTANCE_EMAIL"],
        )
        result = self.bridge.updateContractFramework(self.contract.id, {
            "conclusion_mode": "OFF_PREMISES", "early_performance_requested": True,
        })
        self.assertTrue(result["ok"])
        state = self.bridge.contract_workspace_snapshot()["conditions_b1"]
        self.assertTrue(state["early_performance_visible"])
        self.assertTrue(state["early_performance_requested"])
        self.bridge.updateContractFramework(self.contract.id, {
            "conclusion_mode": "DISTANCE_EMAIL", "early_performance_requested": False,
        })
        state = self.bridge.contract_workspace_snapshot()["conditions_b1"]
        self.assertFalse(state["early_performance_visible"])
        self.assertIsNone(state["early_performance_requested"])

    def test_service_offer_is_closed_required_and_priority_delay_is_conditional(self):
        family = self.template()
        selected = self.version(family, defaults=TemplateDefaults(visits_per_year=2))
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        self.assertEqual(self.contracts.get(self.contract.id).template_version_id, selected.id)
        valid = {
            "visits_per_year": 3, "refrigerant_handling_mode": "PARTNER",
            "included_options": ["DEEP_CLEANING", "DISINFECTION"],
            "priority_breakdown": True, "priority_breakdown_delay": "Sous 48 heures",
        }
        self.assertTrue(self.bridge.updateContractServiceOffer(self.contract.id, valid)["ok"])
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.visits_per_year, 3)
        self.assertEqual(saved.refrigerant_handling_mode, "PARTNER")
        self.assertEqual(saved.included_options, ("DEEP_CLEANING", "DISINFECTION"))
        self.assertEqual(INCLUDED_OPTIONS, frozenset({"DEEP_CLEANING", "DISINFECTION"}))
        self.assertNotIn("priority_breakdown", saved.included_options)
        self.assertEqual(saved.priority_breakdown_delay, "Sous 48 heures")
        off = {**valid, "priority_breakdown": False, "priority_breakdown_delay": "stale"}
        self.assertTrue(self.bridge.updateContractServiceOffer(self.contract.id, off)["ok"])
        self.assertIsNone(self.contracts.get_conditions(self.contract.id).priority_breakdown_delay)
        for bad in (
            {**valid, "visits_per_year": 0},
            {**valid, "refrigerant_handling_mode": "UNKNOWN"},
            {**valid, "included_options": ["CUSTOM"]},
            {**valid, "priority_breakdown_delay": ""},
            {**valid, "technical": True},
        ):
            with self.subTest(bad=bad):
                self.assertFalse(self.bridge.updateContractServiceOffer(self.contract.id, bad)["ok"])

    def test_template_defaults_copy_once_and_failure_never_emits_false_saved_state(self):
        family = self.template()
        first = self.version(family, "1", defaults=TemplateDefaults(visits_per_year=2))
        self.bridge.updateContractFramework(self.contract.id, {"regime": "CONSUMER"})
        self.assertEqual(self.contracts.get_conditions(self.contract.id).visits_per_year, 2)
        valid = {
            "visits_per_year": 4, "refrigerant_handling_mode": "EXCLUDED",
            "included_options": [], "priority_breakdown": False,
            "priority_breakdown_delay": None,
        }
        self.bridge.updateContractServiceOffer(self.contract.id, valid)
        second = self.version(family, "2", defaults=TemplateDefaults(visits_per_year=9))
        self.bridge.selectContractTemplateVersion(self.contract.id, second.id)
        self.assertEqual(self.contracts.get_conditions(self.contract.id).visits_per_year, 4)
        emissions = len(self.states)
        before = self.contracts.get_conditions(self.contract.id)
        with mock.patch.object(
            self.contracts.conditions_repository, "save", side_effect=sqlite3.OperationalError("disk")
        ):
            result = self.bridge.updateContractServiceOffer(self.contract.id, {**valid, "visits_per_year": 5})
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.states), emissions)
        self.assertEqual(self.contracts.get_conditions(self.contract.id), before)
        self.assertEqual(first.defaults.visits_per_year, 2)

    def test_frontend_has_bounded_b1_intents_and_real_conditional_markup(self):
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts"
        js = (root / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        bridge = (root / "ui" / "web_host.py").read_text(encoding="utf-8")
        main_window = (root / "ui" / "main_window.py").read_text(encoding="utf-8")
        for text in (
            "Cadre du contrat", "Modèle & prestations", "Aucun modèle disponible pour ce régime",
            "Confirmez d’abord le régime du contrat.", "Entretien préventif", "Nettoyage approfondi",
            "Désinfection", "Dépannage prioritaire", "Délai d’intervention",
        ):
            self.assertIn(text, js)
        self.assertIn("host.innerHTML = included ? priorityDelayMarkup(previous) : ''", js)
        for intent in (
            "setContractStep", "updateContractFramework", "selectContractTemplateVersion",
            "updateContractServiceOffer", "openContractModels",
        ):
            self.assertIn(f"def {intent}", bridge)
        for forbidden in ("patchContract", "updateContract(field", "updateEntity", "arbitrary JSON"):
            self.assertNotIn(forbidden, bridge)
        for duplicate_frequency in ("annual_frequency", "visit_frequency"):
            self.assertNotIn(duplicate_frequency, bridge)
        self.bridge.openContractModels()
        self.assertEqual(self.legacy_actions[-1], "SETTINGS_MODELS")
        self.assertIn("_show_section('Modèles')", main_window)


if __name__ == "__main__":
    unittest.main()
