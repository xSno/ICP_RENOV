from __future__ import annotations

import subprocess
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from icp_renov_contracts.app import create_application
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.documents.converter import LibreOfficeConverter
from icp_renov_contracts.domain import (
    ContractConditions, ControlledOption, PaymentTermOption, ReviewBlockId, ReviewState,
    TemplateOptionCatalogs, TemplateValidationMetadata, TemplateVersionStatus,
)
from icp_renov_contracts.services import DocumentCapabilities, ReviewService
from icp_renov_contracts.services.capabilities import DocumentCapabilityProbe
from icp_renov_contracts.storage import WorkspaceInspection
from icp_renov_contracts.ui import MainWindow

from test_foundation import scratch
from test_master_data import equipment, organization, site


class FakeCapabilities:
    def __init__(self, docx: bool = False, pdf: bool = True, detail: str = "Conversion PDF disponible"):
        self.value = DocumentCapabilities(docx, pdf, detail, "26.2.5.2" if pdf else None)

    def probe(self): return self.value


class UnavailableWorkspace:
    def inspect(self, path): return WorkspaceInspection(path, False, False)


class ReviewCase(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json"); store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store); self.contracts = self.context.contracts
        self.master = self.context.master_data; self.catalog = self.context.template_catalog
        self.contract = self.contracts.create_draft()
        self.review = ReviewService(self.contracts, self.context.workspace_service, self.context.workspace, FakeCapabilities())

    def tearDown(self): self._scratch.__exit__(None, None, None)

    def create_model(self, validation=TemplateValidationMetadata()):
        template = self.catalog.create_template("Synthétique revue")
        return self.catalog.create_version(
            template, "review-1", TemplateVersionStatus.AVAILABLE, ("CONSUMER",), validation,
            catalogs=TemplateOptionCatalogs(
                ("20",), (PaymentTermOption("DUE", "À échéance", True, False),
                           PaymentTermOption("CUSTOM", "Personnalisé", False, True)),
                (ControlledOption("TRANSFER", "Virement"),),
                (ControlledOption("EMAIL", "E-mail"),), (ControlledOption("BREACH", "Manquement"),),
            ),
        )

    def complete(self, renewal="NONE", validation=TemplateValidationMetadata(
        requires_non_renewal_notice_days=True,
        requires_non_renewal_notice_channels=True,
    )):
        client = self.master.create_client(organization("Client Snapshot"))
        selected_site = self.master.create_site(client.id, site("Site Snapshot"))
        selected_equipment = self.master.create_equipment(selected_site.id, equipment("Unité Snapshot", "Accueil"))
        self.contracts.select_client(self.contract.id, client.id); self.contracts.select_site(self.contract.id, selected_site.id)
        self.contracts.select_equipment(self.contract.id, selected_equipment.id)
        model = self.create_model(validation); self.contracts.change_regime(self.contract.id, "CONSUMER")
        self.contracts.select_template_version(self.contract.id, model.id)
        values = ContractConditions(
            visits_per_year=2, refrigerant_handling_mode="PARTNER", priority_breakdown=False,
            included_area="Paris", business_hours="8h–18h", travel_included=True,
            issue_date="2026-08-13", start_date="2026-09-01", initial_duration_mode="STANDARD",
            initial_duration_months=12, annual_ht="100", vat_rate="20", payment_terms_code="DUE",
            payment_due_days=30, payment_methods=("TRANSFER",), renewal_mode=renewal,
            renewal_period_months=12 if renewal != "NONE" else None,
            non_renewal_notice_days=60 if renewal == "TACIT" else None,
            non_renewal_notice_channels=("EMAIL",) if renewal == "TACIT" else (),
            internal_alert_days=90 if renewal != "NONE" else None,
            renewal_price_rule="FIXED" if renewal != "NONE" else None,
        )
        self.contracts.save_conditions(self.contract.id, values)
        return client, selected_site, selected_equipment, model

    def block(self, result, block_id): return next(block for block in result.blocks if block.id is block_id)


class ReviewStructureTests(ReviewCase):
    def test_exactly_nine_blocks_in_frozen_order(self):
        result = self.review.review(self.contract.id)
        self.assertEqual([block.title for block in result.blocks], [
            "Client & signataire", "Site & équipements", "Cadre du contrat", "Modèle & prestations",
            "Période", "Conditions d’intervention", "Prix & paiement",
            "Renouvellement & fin du contrat", "Conditions particulières",
        ])
        self.assertEqual(len(result.blocks), 9)

    def test_empty_draft_localizes_errors_and_optional_special_terms_is_valid(self):
        result = self.review.review(self.contract.id)
        self.assertFalse(result.data_complete)
        self.assertTrue(all(block.issues for block in result.blocks[:-1]))
        self.assertIs(self.block(result, ReviewBlockId.SPECIAL_TERMS).state, ReviewState.VALID)
        self.assertEqual(self.block(result, ReviewBlockId.SPECIAL_TERMS).summary, "Aucune condition particulière")

    def test_complete_contract_has_all_nine_valid(self):
        self.complete(); result = self.review.review(self.contract.id)
        self.assertTrue(result.data_complete); self.assertTrue(all(block.state is ReviewState.VALID for block in result.blocks))


class SnapshotReviewTests(ReviewCase):
    def test_unicode_client_snapshot_round_trips_through_persistence(self):
        expected = "SYNTHÉTIQUE — CLIENT REVUE"
        client = self.master.create_client(organization(expected))
        self.contracts.select_client(self.contract.id, client.id)

        reopened = build_application_context(
            config_store=MachineConfigStore(self.temporary / "bootstrap.json")
        )

        self.assertEqual(reopened.contracts.get(self.contract.id).client_snapshot.display_name, expected)

    def test_client_site_equipment_review_uses_snapshots_after_master_edit_and_archive(self):
        client, selected_site, selected_equipment, _ = self.complete()
        self.contracts.update_observation(self.contract.id, self.contracts.get(self.contract.id).equipment_items[0].id, "Observation")
        self.master.update_client(client.id, organization("Client maître modifié"))
        self.master.update_site(selected_site.id, site("Site maître modifié"))
        self.master.update_equipment(selected_equipment.id, equipment("Équipement maître modifié", "Toit"))
        self.master.archive_client(client.id); self.master.archive_site(selected_site.id); self.master.archive_equipment(selected_equipment.id)
        result = self.review.review(self.contract.id)
        self.assertIn("Client Snapshot", self.block(result, ReviewBlockId.CLIENT_SIGNATORY).summary)
        equipment_block = self.block(result, ReviewBlockId.SITE_EQUIPMENT)
        self.assertIn("Site Snapshot", equipment_block.summary); self.assertIn("1 avec observation", equipment_block.summary)
        self.assertIs(equipment_block.state, ReviewState.VALID)

    def test_missing_signatory_site_and_equipment_are_localized(self):
        client = self.master.create_client(organization("Sans signataire", proposed_contact_name="", proposed_contact_role=""))
        self.contracts.select_client(self.contract.id, client.id); result = self.review.review(self.contract.id)
        self.assertEqual(len(self.block(result, ReviewBlockId.CLIENT_SIGNATORY).issues), 2)
        messages = [item.message for item in self.block(result, ReviewBlockId.SITE_EQUIPMENT).issues]
        self.assertIn("Sélectionnez un site.", messages); self.assertIn("Aucun équipement sélectionné.", messages)


class ModelContextReviewTests(ReviewCase):
    def test_missing_regime_and_model_are_not_triplicated(self):
        result = self.review.review(self.contract.id)
        context = self.block(result, ReviewBlockId.CONTRACT_CONTEXT); model = self.block(result, ReviewBlockId.MODEL_SERVICES)
        self.assertIn("Choisissez le régime du contrat.", [issue.message for issue in context.issues])
        self.assertEqual(sum(issue.message == "Aucun modèle sélectionné." for issue in model.issues), 1)

    def test_archived_to_validate_and_incompatible_selected_models_are_defensively_invalid(self):
        _, _, _, model = self.complete()
        for status in (TemplateVersionStatus.ARCHIVED, TemplateVersionStatus.TO_VALIDATE):
            with self.subTest(status=status):
                self.catalog.update_status(model.id, status)
                result = self.review.review(self.contract.id)
                self.assertIn("Le modèle sélectionné n’est plus disponible pour ce contrat.",
                              [issue.message for issue in self.block(result, ReviewBlockId.MODEL_SERVICES).issues])
                self.assertFalse(result.generation.checks[0].available)
        self.catalog.update_status(model.id, TemplateVersionStatus.AVAILABLE)
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contract_templates SET contract_type_code='OTHER' WHERE id=?", (model.template_id,))
        result = self.review.review(self.contract.id)
        self.assertIn("Le modèle sélectionné n’est plus disponible pour ce contrat.",
                      [issue.message for issue in self.block(result, ReviewBlockId.MODEL_SERVICES).issues])

    def test_required_conclusion_and_exact_early_performance_context(self):
        metadata = TemplateValidationMetadata(
            ("CONSUMER",), (__import__('icp_renov_contracts.domain', fromlist=['ContextAuthorization']).ContextAuthorization(
                "CONSUMER", "OFF_PREMISES", ("BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE")),),
        )
        self.complete(validation=metadata)
        result = self.review.review(self.contract.id)
        self.assertIn("Renseignez le mode de conclusion.", [i.message for i in self.block(result, ReviewBlockId.CONTRACT_CONTEXT).issues])
        values = asdict(self.contracts.get_conditions(self.contract.id)); values.update(conclusion_mode="OFF_PREMISES", early_performance_requested=True)
        self.contracts.save_conditions(self.contract.id, ContractConditions(**values))
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.CONTRACT_CONTEXT).state, ReviewState.VALID)

    def test_authorized_early_performance_question_requires_an_explicit_decision(self):
        metadata = TemplateValidationMetadata(
            ("CONSUMER",), (__import__('icp_renov_contracts.domain', fromlist=['ContextAuthorization']).ContextAuthorization(
                "CONSUMER", "OFF_PREMISES", ("BLOCK_WITHDRAWAL", "BLOCK_EARLY_PERFORMANCE")),),
        )
        self.complete(validation=metadata)
        values = asdict(self.contracts.get_conditions(self.contract.id)); values.update(conclusion_mode="OFF_PREMISES")
        self.contracts.save_conditions(self.contract.id, ContractConditions(**values))
        issues = [i.message for i in self.block(self.review.review(self.contract.id), ReviewBlockId.CONTRACT_CONTEXT).issues]
        self.assertIn("Indiquez si un démarrage anticipé est demandé.", issues)

    def test_conclusion_not_required_does_not_block(self):
        self.complete(); self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.CONTRACT_CONTEXT).state, ReviewState.VALID)


class ConditionsReviewTests(ReviewCase):
    def setUp(self): super().setUp(); self.complete()

    def save(self, **changes):
        values = asdict(self.contracts.get_conditions(self.contract.id)); values.update(changes)
        with self.context.database.transaction() as connection:
            # Use direct persistence only where testing defensive states rejected by S3.
            if "renewal_price_rule" in changes and changes["renewal_price_rule"] == "INDEXED":
                return ContractConditions(**values)
        self.contracts.save_conditions(self.contract.id, ContractConditions(**values)); return self.contracts.get_conditions(self.contract.id)

    def test_services_missing_and_priority_delay(self):
        self.save(visits_per_year=None, refrigerant_handling_mode=None, priority_breakdown=True, priority_breakdown_delay=None)
        issues = [i.message for i in self.block(self.review.review(self.contract.id), ReviewBlockId.MODEL_SERVICES).issues]
        self.assertIn("Renseignez un nombre de visites valide.", issues); self.assertIn("Choisissez la gestion des fluides.", issues)
        self.assertIn("Renseignez le délai d’intervention prioritaire.", issues)

    def test_period_missing_custom_incoherent_and_standard_valid(self):
        self.save(issue_date=None, start_date=None, initial_duration_mode=None, initial_duration_months=None)
        self.assertGreaterEqual(len(self.block(self.review.review(self.contract.id), ReviewBlockId.PERIOD).issues), 3)
        self.save(issue_date="2026-01-01", start_date="2026-09-01", initial_duration_mode="CUSTOM", initial_end_date="2026-08-01")
        self.assertIn("La date de fin doit être postérieure à la date de prise d’effet.",
                      [i.message for i in self.block(self.review.review(self.contract.id), ReviewBlockId.PERIOD).issues])
        self.save(initial_duration_mode="STANDARD", initial_duration_months=12, initial_end_date=None)
        block = self.block(self.review.review(self.contract.id), ReviewBlockId.PERIOD)
        self.assertIs(block.state, ReviewState.VALID)
        self.assertIn("Du 01/09/2026 au 31/08/2027", block.summary)
        self.assertIn("Durée standard · 12 mois", block.summary)

    def test_intervention_missing_and_optional_fee(self):
        self.save(included_area="", business_hours="", travel_included=None, missed_appointment_fee=None)
        self.assertEqual(len(self.block(self.review.review(self.contract.id), ReviewBlockId.INTERVENTION).issues), 3)
        self.save(included_area="Paris", business_hours="8h–18h", travel_included=False)
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.INTERVENTION).state, ReviewState.VALID)

    def test_price_payment_requiredness_and_calculated_summary(self):
        self.save(annual_ht=None, vat_rate=None, payment_terms_code=None, payment_due_days=None, payment_methods=())
        self.assertGreaterEqual(len(self.block(self.review.review(self.contract.id), ReviewBlockId.PRICE_PAYMENT).issues), 4)
        self.save(annual_ht="100", vat_rate="20", payment_terms_code="DUE", payment_due_days=None, payment_methods=("TRANSFER",))
        self.assertIn("Renseignez le délai de paiement.", [i.message for i in self.block(self.review.review(self.contract.id), ReviewBlockId.PRICE_PAYMENT).issues])
        self.save(payment_due_days=30)
        block = self.block(self.review.review(self.contract.id), ReviewBlockId.PRICE_PAYMENT)
        self.assertIs(block.state, ReviewState.VALID)
        self.assertIn("100,00 € HT", block.summary)
        self.assertIn("TVA 20 % (20,00 €)", block.summary)
        self.assertIn("120,00 € TTC", block.summary)

    def test_custom_payment_text_is_conditional(self):
        self.save(payment_terms_code="CUSTOM", payment_due_days=None, payment_terms_custom_text="")
        self.assertIn("Précisez la modalité de paiement.", [i.message for i in self.block(self.review.review(self.contract.id), ReviewBlockId.PRICE_PAYMENT).issues])
        self.save(payment_terms_custom_text="À réception")
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.PRICE_PAYMENT).state, ReviewState.VALID)

    def test_renewal_none_manual_tacit_requiredness(self):
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.RENEWAL_END).state, ReviewState.VALID)
        self.save(renewal_mode="MANUAL", renewal_period_months=None, renewal_price_rule=None, internal_alert_days=None)
        self.assertEqual(len(self.block(self.review.review(self.contract.id), ReviewBlockId.RENEWAL_END).issues), 3)
        self.save(renewal_period_months=12, renewal_price_rule="FIXED", internal_alert_days=90)
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.RENEWAL_END).state, ReviewState.VALID)
        self.save(renewal_mode="TACIT", non_renewal_notice_days=None, non_renewal_notice_channels=())
        self.assertEqual(len(self.block(self.review.review(self.contract.id), ReviewBlockId.RENEWAL_END).issues), 2)
        self.save(non_renewal_notice_days=60, non_renewal_notice_channels=("EMAIL",))
        self.assertIs(self.block(self.review.review(self.contract.id), ReviewBlockId.RENEWAL_END).state, ReviewState.VALID)

    def test_special_terms_empty_and_populated_are_valid_and_concise(self):
        block = self.block(self.review.review(self.contract.id), ReviewBlockId.SPECIAL_TERMS); self.assertIs(block.state, ReviewState.VALID)
        self.save(special_terms="Très longue condition\nDeuxième ligne")
        block = self.block(self.review.review(self.contract.id), ReviewBlockId.SPECIAL_TERMS)
        self.assertEqual(block.summary, "Conditions particulières renseignées"); self.assertNotIn("Très longue", block.summary)


class ReadinessAndProbeTests(ReviewCase):
    def test_incomplete_and_complete_distinguish_data_from_generation(self):
        incomplete = ReviewService(self.contracts, self.context.workspace_service, self.context.workspace, FakeCapabilities(False, False)).review(self.contract.id)
        self.assertFalse(incomplete.data_complete); self.assertFalse(incomplete.generation_available)
        self.complete()
        real_s4 = ReviewService(self.contracts, self.context.workspace_service, self.context.workspace, FakeCapabilities(False, True)).review(self.contract.id)
        self.assertTrue(real_s4.data_complete); self.assertFalse(real_s4.generation_available)
        synthetic = ReviewService(self.contracts, self.context.workspace_service, self.context.workspace, FakeCapabilities(True, True)).review(self.contract.id)
        self.assertTrue(synthetic.data_complete); self.assertTrue(synthetic.generation_available)
        self.assertEqual(len(synthetic.generation.checks), 4)

    def test_workspace_unavailable_is_separate_check(self):
        self.complete(); result = ReviewService(self.contracts, UnavailableWorkspace(), self.context.workspace, FakeCapabilities(True, True)).review(self.contract.id)
        self.assertTrue(result.data_complete); self.assertFalse(result.generation.checks[1].available)

    @patch.object(DocumentCapabilityProbe, "_find_soffice", return_value=None)
    def test_libreoffice_missing(self, find):
        result = DocumentCapabilityProbe().probe(); self.assertFalse(result.pdf_available); self.assertFalse(result.docx_available)

    @patch.object(DocumentCapabilityProbe, "_find_soffice", return_value=Path("C:/fake/soffice.com"))
    @patch("icp_renov_contracts.services.capabilities.subprocess.run")
    def test_libreoffice_accepted_unexpected_and_probe_error(self, run, find):
        run.return_value = subprocess.CompletedProcess([], 0, "LibreOffice 26.2.5.2", "")
        self.assertTrue(DocumentCapabilityProbe().probe().pdf_available)
        run.return_value = subprocess.CompletedProcess([], 0, "LibreOffice 26.2.6.0", "")
        self.assertFalse(DocumentCapabilityProbe().probe().pdf_available)
        run.side_effect = OSError("probe")
        self.assertEqual(DocumentCapabilityProbe().probe().pdf_detail, "Conversion PDF à vérifier")

    @patch.object(DocumentCapabilityProbe, "_find_soffice", return_value=Path("C:/fake/soffice.com"))
    @patch("icp_renov_contracts.services.capabilities.subprocess.run")
    def test_capability_probe_caches_navigation_checks_but_diagnostic_refreshes(self, run, find):
        run.return_value = subprocess.CompletedProcess([], 0, "LibreOffice 26.2.5.2", "")
        probe = DocumentCapabilityProbe()
        self.assertTrue(probe.probe().pdf_available); self.assertTrue(probe.probe().pdf_available)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertTrue(probe.probe(refresh=True).pdf_available)
        self.assertEqual(run.call_count, 2)

    @patch("icp_renov_contracts.documents.converter.subprocess.run")
    def test_converter_version_probe_is_windowless_and_cached(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "LibreOffice 26.2.5.2", "")
        converter = LibreOfficeConverter(Path("C:/fake/soffice.com"))
        self.assertEqual(converter.version(), "26.2.5.2"); self.assertEqual(converter.version(), "26.2.5.2")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))


class ReviewUiTests(ReviewCase):
    @classmethod
    def setUpClass(cls): cls.application = create_application(["test-review"])

    def setUp(self):
        super().setUp(); self.context.review.capability_probe = FakeCapabilities(False, True)
        self.window = MainWindow(self.context); self.window.show(); self.application.processEvents()
        self.view = self.window.shell.surface("Contrats"); self.view.open_contract(self.contract.id)

    def tearDown(self): self.window.close(); self.application.processEvents(); super().tearDown()

    def test_review_step_enabled_documents_disabled_and_empty_draft_enters(self):
        self.assertEqual([button.isEnabled() for button in self.view.step_buttons], [True, True, True, True])
        self.view.navigate_step(2); self.application.processEvents()
        self.assertEqual(self.view.step_pages.currentIndex(), 2); self.assertEqual(len(self.view.review_view.block_cards), 9)
        self.assertEqual(self.view.review_view.overall.text(), "Informations à compléter avant génération")
        self.assertEqual(len(self.view.review_view.generation_checks), 4)
        self.assertFalse(self.view.review_view.generate_button.isEnabled())
        self.assertFalse(any(isinstance(widget, QDialog) for widget in self.window.findChildren(QDialog)))

    def test_modify_routes_first_two_to_step_one_and_others_to_step_two(self):
        self.view.navigate_step(2); QTest.mouseClick(self.view.review_view.modify_buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.step_pages.currentIndex(), 0)
        self.view.navigate_step(2); QTest.mouseClick(self.view.review_view.modify_buttons[2], Qt.MouseButton.LeftButton)
        self.assertEqual(self.view.step_pages.currentIndex(), 1)

    def test_complete_banner_pipeline_separate_disabled_cta_and_no_side_effect(self):
        self.complete(); self.view.open_contract(self.contract.id); self.view.navigate_step(2); self.application.processEvents()
        self.assertEqual(self.view.review_view.overall.text(), "Toutes les informations nécessaires sont complètes")
        self.assertIn("génération est indisponible", self.view.review_view.distinction.text())
        before = self._artifact_counts(); QTest.mouseClick(self.view.review_view.generate_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self._artifact_counts(), before)

    def test_generation_chain_failure_opens_diagnostic_with_exact_selected_version(self):
        version = self.complete()[-1]; self.view.open_contract(self.contract.id); self.view.navigate_step(2); self.application.processEvents()
        review = self.view.review_view
        self.assertEqual(len(review.generation_checks), 4); self.assertTrue(review.diagnostic_button.isVisible())
        QTest.mouseClick(review.diagnostic_button, Qt.MouseButton.LeftButton); self.application.processEvents()
        settings = self.window.shell.surface("Paramètres")
        self.assertEqual(settings.stack.currentIndex(), settings._indices["Diagnostic génération"])
        self.assertEqual(settings.diagnostic_page.version_id, version.id)

    def test_direct_diagnostic_entry_keeps_model_and_version_unselected(self):
        self.complete(); settings = self.window.shell.surface("Paramètres")
        settings.open_diagnostic(); self.application.processEvents()
        page = settings.diagnostic_page
        self.assertIsNone(page.version_id); self.assertEqual(page.model.currentIndex(), -1)
        self.assertIn("Sélectionnez un modèle", page.source.text())

    def _artifact_counts(self):
        with self.context.database.connection() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return ("contract_documents" in tables, {row[1] for row in connection.execute("PRAGMA table_info(contracts)")})
