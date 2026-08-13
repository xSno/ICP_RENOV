from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialog, QPushButton

from icp_renov_contracts.app import create_application
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.domain import (
    ContextAuthorization, ContractConditions, ControlledOption, PaymentTermOption,
    TemplateDefaults, TemplateOptionCatalogs, TemplateValidationMetadata,
    TemplateVersionStatus, standard_end_date,
)
from icp_renov_contracts.errors import ContractConditionsValidationError, ContractValidationError
from icp_renov_contracts.ui import MainWindow
from icp_renov_contracts.ui.conditions_view import EarlyTerminationEditor

from test_foundation import scratch


def validation(required: bool = True) -> TemplateValidationMetadata:
    return TemplateValidationMetadata(
        ("CONSUMER",) if required else (),
        (ContextAuthorization("CONSUMER", "OFF_PREMISES", ("BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE")),
         ContextAuthorization("CONSUMER", "DISTANCE_EMAIL", ("BLOCK_WITHDRAWAL",))),
    )


def catalogs() -> TemplateOptionCatalogs:
    return TemplateOptionCatalogs(
        vat_rates=("5.5", "20"),
        payment_terms=(PaymentTermOption("DUE", "À échéance", True, False),
                       PaymentTermOption("CUSTOM", "Modalité personnalisée", False, True)),
        payment_methods=(ControlledOption("TRANSFER", "Virement"), ControlledOption("CARD", "Carte")),
        non_renewal_channels=(ControlledOption("EMAIL", "E-mail"), ControlledOption("POST", "Courrier")),
        early_termination_reasons=(ControlledOption("BREACH", "Manquement"), ControlledOption("OTHER", "Autre")),
    )


class ConditionsCase(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json"); store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.contracts = self.context.contracts; self.catalog = self.context.template_catalog
        self.contract = self.contracts.create_draft()
        self.template = self.catalog.create_template("Synthétique habitation")

    def tearDown(self): self._scratch.__exit__(None, None, None)

    def version(self, status=TemplateVersionStatus.AVAILABLE, regimes=("CONSUMER",), required=True,
                defaults=TemplateDefaults(), name="1.0"):
        metadata = validation(required) if "CONSUMER" in regimes else TemplateValidationMetadata()
        return self.catalog.create_version(self.template, name, status, regimes, metadata, defaults, catalogs())

    def select(self, version=None):
        version = version or self.version(); self.contracts.change_regime(self.contract.id, "CONSUMER")
        self.contracts.select_template_version(self.contract.id, version.id); return version


class MigrationFourTests(unittest.TestCase):
    def test_migration_three_to_four_and_reopen_are_deterministic(self):
        with scratch() as temporary:
            path = Path(temporary) / "s2.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:3]:
                    for statement in migration.statements: connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations VALUES (?,?)", (migration.version, "frozen"))
                connection.execute("INSERT INTO contracts(id,status,type_code,created_at_utc,updated_at_utc) VALUES ('draft','DRAFT','CLIMATE_MAINTENANCE','t','t')")
                connection.execute("PRAGMA user_version=3"); connection.commit()
            database = DatabaseService(path); database.initialize(); database.initialize()
            self.assertEqual(database.schema_version(), 6)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 6)
                self.assertEqual(connection.execute("SELECT contract_id FROM contract_conditions").fetchone()[0], "draft")


class TemplateCompatibilityTests(ConditionsCase):
    def test_catalog_has_no_automatic_production_seed(self):
        self.assertEqual(self.contracts.compatible_template_versions(self.contract.id), [])
        with self.context.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM contract_templates").fetchone()[0], 1)

    def test_only_available_exact_regime_and_type_is_selectable(self):
        available = self.version(); pending = self.version(TemplateVersionStatus.TO_VALIDATE, name="pending")
        archived = self.version(TemplateVersionStatus.ARCHIVED, name="archived")
        nonpro = self.version(regimes=("NON_PROFESSIONAL",), name="nonpro")
        self.contracts.change_regime(self.contract.id, "CONSUMER")
        self.assertEqual([item.id for item in self.contracts.compatible_template_versions(self.contract.id)], [available.id])
        for rejected in (pending, archived, nonpro):
            with self.subTest(rejected=rejected.version), self.assertRaises(ContractValidationError):
                self.contracts.select_template_version(self.contract.id, rejected.id)

    def test_consumer_and_nonprofessional_are_never_equivalent(self):
        nonpro = self.version(regimes=("NON_PROFESSIONAL",))
        self.contracts.change_regime(self.contract.id, "CONSUMER")
        self.assertNotIn(nonpro.id, [item.id for item in self.contracts.compatible_template_versions(self.contract.id)])

    def test_selected_version_remains_referenced_when_later_archived(self):
        version = self.select(); self.catalog.update_status(version.id, TemplateVersionStatus.ARCHIVED)
        reopened = build_application_context(config_store=self.context.config_store).contracts.get(self.contract.id)
        self.assertEqual(reopened.template_version_id, version.id)
        self.assertEqual(self.contracts.selected_template_version(self.contract.id).status, TemplateVersionStatus.ARCHIVED)

    def test_regime_change_clears_incompatible_model_and_context_only(self):
        self.select(); values = ContractConditions(conclusion_mode="OFF_PREMISES", early_performance_requested=True,
                                                   included_area="Paris", annual_ht="100")
        self.contracts.save_conditions(self.contract.id, values)
        self.contracts.change_regime(self.contract.id, "PROFESSIONAL")
        contract = self.contracts.get(self.contract.id); saved = self.contracts.get_conditions(self.contract.id)
        self.assertIsNone(contract.template_version_id); self.assertIsNone(saved.conclusion_mode)
        self.assertIsNone(saved.early_performance_requested); self.assertEqual(saved.included_area, "Paris")
        self.assertEqual(saved.annual_ht, "100.00")


class ContextAndDefaultsTests(ConditionsCase):
    def test_conclusion_optional_without_metadata_and_stale_values_cleared(self):
        version = self.version(required=False); self.select(version)
        saved = self.contracts.save_conditions(self.contract.id, ContractConditions(
            conclusion_mode="OFF_PREMISES", early_performance_requested=True))
        self.assertIsNone(saved.conclusion_mode); self.assertIsNone(saved.early_performance_requested)

    def test_invalid_conclusion_rejected_when_required(self):
        self.select()
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, ContractConditions(conclusion_mode="UNKNOWN"))

    def test_early_performance_requires_exact_context_and_withdrawal(self):
        self.select()
        accepted = self.contracts.save_conditions(self.contract.id, ContractConditions(
            conclusion_mode="OFF_PREMISES", early_performance_requested=True))
        self.assertTrue(accepted.early_performance_requested)
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, ContractConditions(
                conclusion_mode="DISTANCE_EMAIL", early_performance_requested=True))
        cleared = self.contracts.save_conditions(self.contract.id, ContractConditions(
            conclusion_mode="DISTANCE_EMAIL", early_performance_requested=False))
        self.assertIsNone(cleared.early_performance_requested)

    def test_model_prefills_only_empty_values_and_change_preserves_entries(self):
        first = self.version(defaults=TemplateDefaults(visits_per_year=2, included_area="Zone modèle"), name="1")
        self.contracts.change_regime(self.contract.id, "CONSUMER")
        self.contracts.save_conditions(self.contract.id, ContractConditions(visits_per_year=4, business_hours="8h-18h"))
        self.contracts.select_template_version(self.contract.id, first.id)
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual(saved.visits_per_year, 4); self.assertEqual(saved.included_area, "Zone modèle")
        second = self.version(defaults=TemplateDefaults(visits_per_year=9, included_area="Autre"), name="2")
        self.contracts.select_template_version(self.contract.id, second.id)
        saved = self.contracts.get_conditions(self.contract.id)
        self.assertEqual((saved.visits_per_year, saved.included_area, saved.business_hours), (4, "Zone modèle", "8h-18h"))


class ServicePeriodPricingTests(ConditionsCase):
    def setUp(self): super().setUp(); self.select()

    def test_services_controlled_values_and_priority_cascade(self):
        saved = self.contracts.save_conditions(self.contract.id, ContractConditions(
            visits_per_year=2, included_options=("DEEP_CLEANING", "DISINFECTION"),
            refrigerant_handling_mode="PARTNER", priority_breakdown=True,
            priority_breakdown_delay="Sous 48 heures", additional_exclusions="Accès toiture\nnon inclus"))
        self.assertEqual(saved.priority_breakdown_delay, "Sous 48 heures")
        off = ContractConditions(**{**asdict(saved), "priority_breakdown": False})
        self.assertIsNone(self.contracts.save_conditions(self.contract.id, off).priority_breakdown_delay)
        for bad in (ContractConditions(visits_per_year=0), ContractConditions(included_options=("UNKNOWN",)),
                    ContractConditions(refrigerant_handling_mode="UNKNOWN")):
            with self.subTest(bad=bad), self.assertRaises(ContractConditionsValidationError):
                self.contracts.save_conditions(self.contract.id, bad)

    def test_standard_custom_and_date_calculations(self):
        self.assertEqual(standard_end_date("2026-09-01", 12), "2027-08-31")
        self.assertEqual(standard_end_date("2026-01-31", 1), "2026-02-27")
        self.assertEqual(standard_end_date("2024-02-29", 12), "2025-02-27")
        standard = self.contracts.save_conditions(self.contract.id, ContractConditions(
            start_date="2026-09-01", initial_duration_mode="STANDARD", initial_duration_months=12,
            initial_end_date="2099-01-01"))
        self.assertIsNone(standard.initial_end_date); self.assertEqual(standard.resolved_end_date, "2027-08-31")
        custom = self.contracts.save_conditions(self.contract.id, ContractConditions(
            start_date="2026-09-01", initial_duration_mode="CUSTOM", initial_duration_months=12,
            initial_end_date="2027-06-30"))
        self.assertIsNone(custom.initial_duration_months); self.assertEqual(custom.resolved_end_date, "2027-06-30")
        self.assertNotIn("signature_date", ContractConditions.__dataclass_fields__)

    def test_pricing_is_calculated_from_two_truths_and_catalog(self):
        saved = self.contracts.save_conditions(self.contract.id, ContractConditions(annual_ht="100", vat_rate="20"))
        self.assertEqual(str(saved.vat_amount), "20.00"); self.assertEqual(str(saved.annual_ttc), "120.00")
        self.assertNotIn("vat_amount", ContractConditions.__dataclass_fields__)
        self.assertNotIn("annual_ttc", ContractConditions.__dataclass_fields__)
        for bad in (ContractConditions(annual_ht="-1"), ContractConditions(vat_rate="10")):
            with self.assertRaises(ContractConditionsValidationError): self.contracts.save_conditions(self.contract.id, bad)

    def test_payment_catalog_dependencies_methods_and_missed_fee(self):
        due = self.contracts.save_conditions(self.contract.id, ContractConditions(
            payment_terms_code="DUE", payment_due_days=30, payment_terms_custom_text="ignored",
            payment_methods=("TRANSFER",), missed_appointment_fee="45"))
        self.assertEqual(due.payment_due_days, 30); self.assertEqual(due.payment_terms_custom_text, "")
        custom = self.contracts.save_conditions(self.contract.id, ContractConditions(
            payment_terms_code="CUSTOM", payment_due_days=12, payment_terms_custom_text="À réception"))
        self.assertIsNone(custom.payment_due_days); self.assertEqual(custom.payment_terms_custom_text, "À réception")
        self.assertIsNone(self.contracts.save_conditions(self.contract.id, ContractConditions()).missed_appointment_fee)
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, ContractConditions(payment_methods=("CASH",)))

    def test_renewal_modes_clear_only_irrelevant_fields_and_indexed_rejected(self):
        base = ContractConditions(renewal_mode="TACIT", renewal_period_months=12, non_renewal_notice_days=60,
                                  non_renewal_notice_channels=("EMAIL",), internal_alert_days=90,
                                  renewal_price_rule="FIXED")
        tacit = self.contracts.save_conditions(self.contract.id, base)
        self.assertEqual((tacit.non_renewal_notice_days, tacit.internal_alert_days), (60, 90))
        manual = self.contracts.save_conditions(self.contract.id, ContractConditions(**{**asdict(base), "renewal_mode": "MANUAL"}))
        self.assertIsNone(manual.non_renewal_notice_days); self.assertEqual(manual.non_renewal_notice_channels, ())
        none = self.contracts.save_conditions(self.contract.id, ContractConditions(**{**asdict(base), "renewal_mode": "NONE"}))
        self.assertIsNone(none.renewal_period_months); self.assertIsNone(none.internal_alert_days)
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, ContractConditions(renewal_mode="MANUAL", renewal_price_rule="INDEXED"))

    def test_early_termination_catalog_and_special_terms_plain_text(self):
        saved = self.contracts.save_conditions(self.contract.id, ContractConditions(
            early_termination_reason_codes=("BREACH",), early_termination_custom_text="Autre motif",
            breach_cure_period_days=15, special_terms="Ligne 1\n**texte brut**"))
        self.assertEqual(saved.special_terms, "Ligne 1\n**texte brut**")
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, ContractConditions(early_termination_reason_codes=("UNKNOWN",)))


class ConditionsUiTests(ConditionsCase):
    @classmethod
    def setUpClass(cls): cls.application = create_application(["test-conditions"])

    def setUp(self):
        super().setUp(); self.window = MainWindow(self.context); self.window.show(); self.application.processEvents()
        self.view = self.window.shell.surface("Contrats"); self.view.open_contract(self.contract.id)
        self.conditions = self.view.conditions_view

    def tearDown(self): self.window.close(); self.application.processEvents(); super().tearDown()

    def enter_conditions(self): self.view.navigate_step(1); self.application.processEvents()

    def test_only_steps_one_and_two_are_functional_and_no_later_workflow(self):
        self.assertEqual([button.isEnabled() for button in self.view.step_buttons], [True, True, True, True])
        self.enter_conditions(); self.assertEqual(self.view.step_pages.currentIndex(), 1)
        text = " ".join(button.text() for button in self.view.findChildren(QPushButton))
        self.assertNotIn("Révision", text)
        self.assertFalse(self.view.review_view.generate_button.isEnabled())

    def test_regime_only_in_conditions_and_models_are_filtered(self):
        available = self.version(); self.version(TemplateVersionStatus.TO_VALIDATE, name="pending")
        self.enter_conditions(); self.assertFalse(self.conditions.model.isVisible())
        _set = self.conditions.regime.findData("CONSUMER"); self.conditions.regime.setCurrentIndex(_set); self.application.processEvents()
        self.assertTrue(self.conditions.model.isVisible())
        self.assertEqual([self.conditions.model.itemData(i) for i in range(self.conditions.model.count()) if self.conditions.model.itemData(i)], [available.id])
        self.window.shell.navigate("Clients & installations"); self.application.processEvents()
        self.assertFalse(any(widget.objectName() == "contractRegime" for widget in self.window.shell.surface("Clients & installations").findChildren(QComboBox)))

    def test_no_model_state_and_context_sensitive_controls(self):
        self.enter_conditions(); self.conditions.regime.setCurrentIndex(self.conditions.regime.findData("PROFESSIONAL")); self.application.processEvents()
        self.assertEqual(self.conditions.model_state.text(), "Aucun modèle disponible pour ce régime")
        self.contracts.change_regime(self.contract.id, None); version = self.version(); self.conditions.load(self.contract.id)
        self.conditions.regime.setCurrentIndex(self.conditions.regime.findData("CONSUMER")); self.application.processEvents()
        self.conditions.model.setCurrentIndex(self.conditions.model.findData(version.id)); self.application.processEvents()
        self.assertTrue(self.conditions.conclusion.isVisible()); self.conditions.conclusion.setCurrentIndex(self.conditions.conclusion.findData("DISTANCE_EMAIL"))
        self.assertFalse(self.conditions.early_performance.isVisible())
        self.conditions.conclusion.setCurrentIndex(self.conditions.conclusion.findData("OFF_PREMISES")); self.assertTrue(self.conditions.early_performance.isVisible())

    def test_dynamic_services_period_pricing_renewal_and_persistence(self):
        version = self.select(); self.enter_conditions(); self.conditions.load(self.contract.id)
        self.conditions.priority.setCurrentIndex(self.conditions.priority.findData(False)); self.assertFalse(self.conditions.priority_delay.isVisible())
        self.conditions.priority.setCurrentIndex(self.conditions.priority.findData(True)); self.assertTrue(self.conditions.priority_delay.isVisible())
        self.conditions.duration_mode.setCurrentIndex(self.conditions.duration_mode.findData("STANDARD")); self.conditions.start_date.setText("2026-09-01"); self.conditions.duration_months.setText("12")
        self.assertTrue(self.conditions.end_date.isReadOnly()); self.assertEqual(self.conditions.end_date.text(), "2027-08-31")
        self.conditions.duration_mode.setCurrentIndex(self.conditions.duration_mode.findData("CUSTOM")); self.assertFalse(self.conditions.end_date.isReadOnly())
        self.conditions.annual_ht.setText("100"); self.conditions.vat_rate.setCurrentIndex(self.conditions.vat_rate.findData("20"))
        self.assertEqual(self.conditions.vat_amount.text(), "20.00 €"); self.assertEqual(self.conditions.annual_ttc.text(), "120.00 €")
        self.conditions.renewal_mode.setCurrentIndex(self.conditions.renewal_mode.findData("NONE")); self.assertFalse(self.conditions.renewal_period.isVisible())
        self.conditions.renewal_mode.setCurrentIndex(self.conditions.renewal_mode.findData("MANUAL")); self.assertTrue(self.conditions.renewal_period.isVisible()); self.assertFalse(self.conditions.notice_days.isVisible())
        self.conditions.renewal_mode.setCurrentIndex(self.conditions.renewal_mode.findData("TACIT")); self.assertTrue(self.conditions.notice_days.isVisible())
        self.conditions.special_terms.setPlainText("Ligne A\nLigne B"); self.assertTrue(self.conditions.save())
        self.view.navigate_step(0); self.view.navigate_step(1); self.assertEqual(self.conditions.special_terms.toPlainText(), "Ligne A\nLigne B")

    def test_early_termination_is_in_app_drawer(self):
        self.select(); self.enter_conditions(); self.conditions.load(self.contract.id)
        QTest.mouseClick(self.conditions.early_button, Qt.MouseButton.LeftButton); self.application.processEvents()
        self.assertIsInstance(self.view.active_drawer, EarlyTerminationEditor)
        self.assertIs(self.view.active_drawer.window(), self.window)
        self.assertFalse(any(isinstance(widget, QDialog) for widget in self.window.findChildren(QDialog)))
