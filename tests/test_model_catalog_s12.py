from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
import sqlite3
import unittest
import uuid

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QLineEdit, QPushButton, QTableWidget

from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.documents import (
    NonOfficialModelValidationRunner, ProductionDocxRenderer,
    StaticCompanyDocumentDataProvider, TemplateSourceStore,
)
from icp_renov_contracts.documents.registry import FIELD_FILE, FIELDS
from icp_renov_contracts.documents.ooxml import read_package, write_package
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.documents.formatters import prepare_context
from icp_renov_contracts.domain import (
    EXTERNAL_GATE_CODES, ExternalGateStatus, ReviewEvidenceStatus,
    TemplateVersionStatus, ValidationCheckStatus,
)
from icp_renov_contracts.errors import ContractValidationError
from icp_renov_contracts.repositories import (
    CompanySettingsRepository, TemplateCatalogRepository, TemplateValidationRepository,
)
from icp_renov_contracts.services import CompanySettingsService, TemplateCatalogService
from icp_renov_contracts.storage import WorkspaceService
from icp_renov_contracts.ui.models_settings_view import AddModelDialog, ModelsSettingsPage, NewVersionDialog
from icp_renov_contracts.ui.settings_view import CompanySettingsView
from icp_renov_contracts.ui.styles import application_stylesheet


class FakeConverter:
    def __init__(self, available=True): self.enabled = available
    def available(self): return self.enabled
    def convert(self, docx, pdf):
        if not self.enabled: raise DocumentGenerationError("converter_unavailable", "unavailable")
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")


COMPANY = {key.split(".", 1)[1]: "SYNTHÉTIQUE" for key in FIELDS if key.startswith("company.")}
COMPANY.update({
    "logo": "", "share_capital": "1000", "trade_name": "ICP Test", "legal_name": "ICP TEST SAS",
    "address_line1": "1 rue du Test", "postal_code": "75001", "city": "Paris", "country": "France",
    "registered_address": "1 rue du Test, 75001 Paris", "phone": "0102030405", "email": "test@example.invalid",
})


class ModelCatalogS12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(["models-s12"])

    def setUp(self):
        base = Path(__file__).parent / "tmp"; base.mkdir(exist_ok=True)
        self.root = base / uuid.uuid4().hex; self.root.mkdir()
        self.workspace = WorkspaceService().ensure(self.root / "workspace")
        self.database = DatabaseService(self.workspace.database_path); self.database.initialize()
        self.repository = TemplateCatalogRepository(self.database)
        self.validation_repository = TemplateValidationRepository(self.database)
        self.store = TemplateSourceStore(self.workspace.root)
        self.provider = StaticCompanyDocumentDataProvider(COMPANY)
        self.converter = FakeConverter()
        runner = NonOfficialModelValidationRunner(self.workspace.root, ProductionDocxRenderer(), self.converter, self.provider)
        self.service = TemplateCatalogService(
            self.repository, self.store, self.validation_repository,
            validation_runner=runner, company_provider=self.provider,
        )
        templates = Path(__file__).parents[1] / "templates"
        self.contract_source = next(templates.glob("*HABITATION*.docx"))
        self.sheet_source = templates / "ICP_RENOV_TEMPLATE_FICHE_INTERVENTION_V1_0.docx"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add_contract(self, name="Contrat synthétique", version="1.0"):
        return self.service.add_model(name, "CONTRACT", version, self.contract_source, ("CONSUMER",))

    def make_ready(self, version_id: str):
        self.service.confirm_regime(version_id, "CONSUMER", "Validation synthétique indépendante")
        self.service.set_context_review(version_id, ReviewEvidenceStatus.CONFIRMED)
        for code in EXTERNAL_GATE_CODES:
            self.service.set_external_gate(version_id, code, ExternalGateStatus.NOT_APPLICABLE, "Décision synthétique explicite")
        self.service.set_external_content(
            version_id, ExternalGateStatus.CONFIRMED, "Validateur synthétique externe",
            "2026-08-17", "Corpus synthétique S12", "S12-REF", "",
        )
        result = self.service.control_structure(version_id); self.assertTrue(result.passed)
        render = self.service.test_generation(version_id)
        self.service.set_visual_review(version_id, ReviewEvidenceStatus.CONFIRMED)
        return render

    def test_frozen_field_registry_matches_the_canonical_baseline(self):
        canonical = FIELD_FILE.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), "11542d5ec835a774232ba00e2ffb588f1c4ef204a4403bb4a112db38b189ce49")
        for forbidden in ("contract.visits_label", "contract.breach_cure_period_clause", "contract.special_terms_display"):
            self.assertNotIn(forbidden, FIELDS)

    def test_migration_eleven_over_ten_preserves_versions_and_has_one_authority(self):
        legacy_path = self.root / "legacy-v10.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute("PRAGMA foreign_keys=ON")
        for migration in MIGRATIONS[:10]:
            for statement in migration.statements: connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version,applied_at_utc) VALUES (?,CURRENT_TIMESTAMP)", (migration.version,))
        connection.execute("INSERT INTO contract_templates VALUES ('t','Existant','CONTRACT','CLIMATE_MAINTENANCE','2026-01-01')")
        connection.execute("INSERT INTO contract_template_versions(id,template_id,version,version_status,allowed_client_regimes_json,validation_metadata_json,defaults_json,option_catalogs_json,created_at_utc,updated_at_utc,source_relpath,source_hash,required_company_fields_json,required_intervention_fields_json) VALUES ('v','t','1.0','TO_VALIDATE','[]','{}','{}','{}','2026-01-01','2026-01-01',NULL,NULL,'[]','[]')")
        connection.commit(); connection.close()
        migrated = DatabaseService(legacy_path); migrated.initialize(); migrated.initialize()
        with migrated.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 12)
            self.assertEqual(connection.execute("SELECT functional_name FROM contract_templates WHERE id='t'").fetchone()[0], "Existant")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM template_version_validation WHERE version_id='v'").fetchone()[0], 1)

    def test_add_contract_and_sheet_are_atomic_governed_and_start_unconfirmed(self):
        before = hashlib.sha256(self.contract_source.read_bytes()).hexdigest()
        contract = self.add_contract(); sheet = self.service.add_model("Fiche synthétique", "INTERVENTION_SHEET", "A", self.sheet_source)
        self.assertEqual(hashlib.sha256(self.contract_source.read_bytes()).hexdigest(), before)
        self.assertEqual((contract.status, contract.allowed_client_regimes, contract.target_client_regimes), (TemplateVersionStatus.TO_VALIDATE, (), ("CONSUMER",)))
        self.assertEqual((sheet.document_kind, sheet.contract_type_code, sheet.allowed_client_regimes), ("INTERVENTION_SHEET", "", ()))
        for item in (contract, sheet):
            self.assertFalse(Path(item.source_relpath).is_absolute()); self.assertEqual(self.store.verify(item.source_relpath, item.source_hash).name, Path(item.source_relpath).name)
            self.assertEqual(self.service.validation_record(item.id).structure_status, ValidationCheckStatus.NOT_RUN)
        invalid = self.root / "invalid.docx"; invalid.write_bytes(b"not a docx")
        with self.database.connection() as connection: counts = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("contract_templates", "contract_template_versions"))
        with self.assertRaises(Exception): self.service.add_model("Cassé", "CONTRACT", "1", invalid)
        with self.database.connection() as connection: self.assertEqual(tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("contract_templates", "contract_template_versions")), counts)

    def test_vat_catalog_defaults_to_twenty_and_stays_administrable_when_available(self):
        version = self.add_contract("TVA administrable", "1")
        self.assertEqual(version.catalogs.vat_rates, ("20",))
        self.assertEqual(tuple(item.code for item in version.catalogs.payment_terms), ("DUE", "CUSTOM"))
        self.assertEqual(tuple(item.code for item in version.catalogs.payment_methods), ("TRANSFER", "CHEQUE"))
        saved = self.service.set_vat_rates(version.id, ("5,5", "20.0", "20"))
        self.assertEqual(saved.catalogs.vat_rates, ("5.5", "20"))
        self.make_ready(version.id); self.service.make_available(version.id)
        updated = self.service.set_vat_rates(version.id, ("10", "20"))
        self.assertEqual(updated.catalogs.vat_rates, ("10", "20"))
        with self.assertRaises(ContractValidationError): self.service.set_vat_rates(version.id, ())
        with self.assertRaises(ContractValidationError): self.service.set_vat_rates(version.id, ("invalid",))

        page = ModelsSettingsPage(self.service); page.open_version(version.id); self.application.processEvents()
        detail = page.stack.currentWidget(); field = detail.findChild(QLineEdit, "templateVatRates")
        self.assertIsNotNone(field); self.assertTrue(field.isEnabled()); self.assertEqual(field.text(), "10 ; 20")
        field.setText("5,5 ; 20"); detail._save_vat_rates(); self.application.processEvents()
        self.assertEqual(self.service.get_version(version.id).catalogs.vat_rates, ("5.5", "20"))
        self.assertEqual(detail.feedback.text(), "Taux de TVA enregistrés.")
        self.service.set_payment_options(version.id, ("DUE",), ("TRANSFER", "CHEQUE"))
        self.assertEqual(tuple(item.code for item in self.service.get_version(version.id).catalogs.payment_terms), ("DUE",))
        self.assertEqual(tuple(item.code for item in self.service.get_version(version.id).catalogs.payment_methods), ("TRANSFER", "CHEQUE"))
        with self.assertRaises(ContractValidationError): self.service.set_payment_options(version.id, (), ("TRANSFER",))
        with self.assertRaises(ContractValidationError): self.service.set_payment_options(version.id, ("DUE",), ("UNKNOWN",))
        page.open_version(version.id); self.application.processEvents(); detail = page.stack.currentWidget()
        self.assertTrue(detail.findChild(QCheckBox, "templatePaymentTerm_DUE").isChecked())
        self.assertTrue(detail.findChild(QCheckBox, "templatePaymentMethod_TRANSFER").isChecked())
        page.close()

    def test_non_official_validation_fixture_uses_a_twenty_percent_decimal_rate(self):
        version = self.add_contract("TVA validation non officielle", "1")
        context = self.service.validation_runner._context(version, 1, observations=False)
        pricing = prepare_context(context)["pricing"]
        self.assertEqual(
            (pricing["annual_ht"], pricing["vat_rate"], pricing["vat_amount"], pricing["annual_ttc"]),
            ("100,00", "20", "20,00", "120,00"),
        )
        self.assertNotIn("2000", " ".join(str(pricing[key]) for key in ("vat_rate", "vat_amount", "annual_ttc")))

    def test_non_official_validation_preserves_company_unicode_in_rendered_docx(self):
        provider = StaticCompanyDocumentDataProvider({
            **COMPANY,
            "registered_address": "1138 boulevard Jean Moulin, 83700 Saint-Rapha\u00ebl, France",
            "city": "Saint-Rapha\u00ebl",
            "signatory_name": "Pascal Romano",
            "signatory_role": "G\u00e9rant",
        })
        runner = NonOfficialModelValidationRunner(self.workspace.root, ProductionDocxRenderer(), self.converter, provider)
        version = self.add_contract("Unicode validation non officielle", "1")
        result = runner.run(version, self.store.verify(version.source_relpath, version.source_hash))
        rendered = self.workspace.root / result.evidence_paths[0]
        payload = b"".join(read_package(rendered).values())
        self.assertIn("Saint-Rapha\u00ebl".encode("utf-8"), payload)
        self.assertIn("G\u00e9rant".encode("utf-8"), payload)
        self.assertNotIn(b"Saint-Rapha?l", payload)
        self.assertNotIn(b"G?rant", payload)

    def test_external_validation_qt_string_statuses_are_normalized_and_reopen_as_primitives(self):
        version = self.add_contract("Validation Qt", "1")
        page = ModelsSettingsPage(self.service); page.open_version(version.id); self.application.processEvents()
        detail = page.stack.currentWidget()

        self.assertEqual(type(detail.external_content_status.currentData()), str)
        detail.external_content_status.setCurrentIndex(detail.external_content_status.findData("CONFIRMED"))
        detail.validator.setText("Validation externe synthétique")
        detail.validation_date.setText("2026-08-22")
        detail.scope.setText("Contrat de test")
        detail.external_reference.setText("QT-BOUNDARY-1")
        statuses = ("CONFIRMED", "NOT_APPLICABLE", "TO_REVIEW")
        expected_statuses = {}
        for index, (code, (combo, reference)) in enumerate(detail.gate_controls.items()):
            status = statuses[index % len(statuses)]
            expected_statuses[code] = ExternalGateStatus(status)
            self.assertEqual(type(combo.currentData()), str)
            combo.setCurrentIndex(combo.findData(status))
            reference.setText(f"preuve-{index}")

        detail._save_external(); self.application.processEvents()
        record = self.service.validation_record(version.id)
        self.assertIs(record.external_content_status, ExternalGateStatus.CONFIRMED)
        self.assertEqual(record.external_validator, "Validation externe synthétique")
        self.assertEqual({item.code: item.status for item in record.external_gates}, expected_statuses)
        self.assertEqual(detail.feedback.text(), "Validation externe enregistrée.")

        page.open_version(version.id); self.application.processEvents()
        reopened = page.stack.currentWidget()
        self.assertEqual(reopened.external_content_status.currentData(), "CONFIRMED")
        self.assertEqual(reopened.gate_controls[EXTERNAL_GATE_CODES[0]][0].currentData(), "CONFIRMED")
        with self.assertRaisesRegex(ContractValidationError, "external validation status"):
            self.service.set_external_gate(version.id, EXTERNAL_GATE_CODES[0], "INVALID", "preuve")
        with self.assertRaisesRegex(ContractValidationError, "external validation status"):
            self.service.set_external_content(version.id, "INVALID")
        page.close()

    def test_initial_version_is_explicit_editable_literal_and_scoped_to_its_family(self):
        dialog = AddModelDialog()
        self.assertIn("Version initiale", [label.text() for label in dialog.findChildren(QLabel)])
        self.assertIn("Annuler", [button.text() for button in dialog.findChildren(QPushButton)])
        self.assertNotIn("Cancel", [button.text() for button in dialog.findChildren(QPushButton)])
        self.assertFalse(dialog.version.isReadOnly())
        self.assertTrue(dialog.version.property("required"))
        self.assertEqual(dialog.version.text(), "1")
        dialog.version.setText("1.0")
        self.assertEqual(dialog.values()[2], "1.0")
        dialog.close()

        first = self.service.add_model("Famille décimale", "CONTRACT", " 1.0 ", self.contract_source)
        other = self.service.add_model("Famille millésimée", "CONTRACT", "2026.1", self.contract_source)
        literal = self.service.create_new_version(first.id, "1", self.contract_source)

        self.assertEqual((first.version, other.version, literal.version), ("1.0", "2026.1", "1"))
        self.assertNotEqual(first.template_id, other.template_id)
        self.assertEqual(literal.template_id, first.template_id)
        with self.assertRaises(Exception):
            self.service.create_new_version(first.id, "1.0", self.contract_source)

    def test_detail_actions_follow_state_and_blocked_primary_action_uses_disabled_tokens(self):
        incomplete = self.add_contract("Contrôles incomplets", "1")
        ready = self.add_contract("Prêt", "1"); self.make_ready(ready.id)
        available = self.add_contract("Disponible", "1"); self.make_ready(available.id); self.service.make_available(available.id)
        archived = self.add_contract("Archivé", "1"); self.service.archive(archived.id)
        page = ModelsSettingsPage(self.service)

        def actions(version_id, text):
            page.open_version(version_id); self.application.processEvents()
            return [button for button in page.stack.currentWidget().findChildren(QPushButton) if button.text() == text and not button.isHidden()]

        blocked_make = actions(incomplete.id, "Rendre disponible")
        self.assertEqual(len(blocked_make), 1); self.assertFalse(blocked_make[0].isEnabled())
        self.assertEqual(len(actions(incomplete.id, "Archiver")), 1)
        ready_make = actions(ready.id, "Rendre disponible")
        self.assertEqual(len(ready_make), 1); self.assertTrue(ready_make[0].isEnabled())
        self.assertEqual(len(actions(ready.id, "Archiver")), 1)
        self.assertEqual(actions(available.id, "Rendre disponible"), [])
        available_archive = actions(available.id, "Archiver")
        self.assertEqual(len(available_archive), 1); self.assertTrue(available_archive[0].isEnabled())
        self.assertEqual(actions(archived.id, "Rendre disponible"), [])
        self.assertEqual(actions(archived.id, "Archiver"), [])
        with self.assertRaises(Exception): self.service.make_available(archived.id)
        stylesheet = application_stylesheet()
        self.assertIn("QPushButton#primaryButton:disabled", stylesheet)
        self.assertIn("background: #D8E1E4", stylesheet)
        page.close()

    def test_detail_uses_french_company_type_and_timestamp_labels(self):
        version = self.add_contract("Libellés métier", "V1")
        self.service.set_required_fields(version.id, ("ape_code", "complaints_contact", "signatory_name", "insurer_name"))
        with self.database.transaction() as connection:
            connection.execute("UPDATE contract_template_versions SET created_at_utc=? WHERE id=?", ("2026-08-17T08:09:10+00:00", version.id))
            connection.execute("UPDATE template_version_validation SET last_validation_at_utc=? WHERE version_id=?", ("2026-08-17T17:11:00+00:00", version.id))
        self.service.last_use = lambda version_id: "2026-08-16T10:30:00+00:00"
        page = ModelsSettingsPage(self.service); page.open_version(version.id); self.application.processEvents()
        labels = [label.text() for label in page.stack.currentWidget().findChildren(QLabel)]
        for expected in (
            "Entretien de climatisation (CLIMATE_MAINTENANCE)",
            "17/08/2026", "17/08/2026 17:11", "16/08/2026 10:30",
        ):
            self.assertIn(expected, labels)
        for business, canonical in (
            ("Code APE / NAF", "company.ape_code"),
            ("Contact réclamations", "company.complaints_contact"),
            ("Nom du signataire par défaut", "company.signatory_name"),
            ("Assureur", "company.insurer_name"),
        ):
            visible = next(label for label in labels if canonical in label)
            self.assertTrue(visible.startswith(f"{business} ({canonical}) · "))
            self.assertTrue(visible.endswith(("présent", "manquant")))
        page.close()

    def test_new_version_preserves_family_lineage_and_resets_all_confirmed_truth(self):
        first = self.add_contract(); self.service.confirm_regime(first.id, "CONSUMER", "preuve")
        self.service.set_context_review(first.id, ReviewEvidenceStatus.CONFIRMED)
        self.service.set_external_gate(first.id, EXTERNAL_GATE_CODES[0], ExternalGateStatus.CONFIRMED, "preuve")
        second = self.service.create_new_version(first.id, "2026.1", self.contract_source)
        reopened = self.service.get_version(first.id); record = self.service.validation_record(second.id)
        self.assertEqual((second.template_id, second.previous_version_id), (first.template_id, first.id))
        self.assertEqual(reopened.allowed_client_regimes, ("CONSUMER",)); self.assertEqual(second.allowed_client_regimes, ())
        self.assertEqual(second.target_client_regimes, first.target_client_regimes)
        self.assertEqual((record.structure_status, record.render_status, record.external_gates, record.regime_confirmations), (ValidationCheckStatus.NOT_RUN, ValidationCheckStatus.NOT_RUN, (), ()))
        with self.assertRaises(Exception): self.service.create_new_version(first.id, "2026.1", self.contract_source)

    def test_regimes_are_independent_and_zero_confirmed_blocks_availability(self):
        version = self.add_contract()
        self.assertFalse(next(item for item in self.service.evaluate_availability(version.id).items if item.code == "regime").passed)
        self.service.confirm_regime(version.id, "CONSUMER", "preuve consommateur")
        saved = self.service.get_version(version.id)
        self.assertEqual(saved.allowed_client_regimes, ("CONSUMER",))
        self.assertNotIn("NON_PROFESSIONAL", saved.allowed_client_regimes); self.assertNotIn("PROFESSIONAL", saved.allowed_client_regimes)

    def test_context_integrity_external_statuses_and_deferred_indexation_are_closed(self):
        version = self.add_contract(); self.service.confirm_regime(version.id, "CONSUMER", "preuve")
        from icp_renov_contracts.domain import ContextAuthorization
        with self.assertRaises(Exception):
            self.service.set_context_review(version.id, ReviewEvidenceStatus.CONFIRMED, (ContextAuthorization("CONSUMER", "OFF_PREMISES", ("BLOCK_EARLY_PERFORMANCE",)),))
        self.service.set_context_review(version.id, ReviewEvidenceStatus.CONFIRMED)
        self.service.set_external_gate(version.id, EXTERNAL_GATE_CODES[0], ExternalGateStatus.REJECTED, "rejet")
        self.assertFalse(next(item for item in self.service.evaluate_availability(version.id).items if item.code == "external_gates").passed)
        with self.assertRaises(Exception): self.service.set_external_gate(version.id, "LEGAL_PRICE_INDEXATION", ExternalGateStatus.CONFIRMED)

    def test_review_statuses_normalize_controlled_ui_strings(self):
        version = self.add_contract(); self.service.confirm_regime(version.id, "CONSUMER", "preuve")
        self.service.set_context_review(version.id, "CONFIRMED")
        self.assertIs(self.service.validation_record(version.id).context_review_status, ReviewEvidenceStatus.CONFIRMED)
        self.assertTrue(self.service.control_structure(version.id).passed); self.service.test_generation(version.id)
        self.service.set_visual_review(version.id, "CONFIRMED")
        self.assertIs(self.service.validation_record(version.id).visual_review_status, ReviewEvidenceStatus.CONFIRMED)
        with self.assertRaises(ContractValidationError): self.service.set_visual_review(version.id, "UNCONTROLLED")

    def test_company_requiredness_is_version_specific_and_unrelated_fields_do_not_block(self):
        provider = StaticCompanyDocumentDataProvider({"phone": "0102030405", "insurer_name": ""})
        service = TemplateCatalogService(
            self.repository, self.store, self.validation_repository,
            validation_runner=self.service.validation_runner, company_provider=provider,
        )
        phone = service.add_model("Téléphone seulement", "CONTRACT", "1", self.contract_source)
        insurer = service.add_model("Téléphone et assureur", "CONTRACT", "1", self.contract_source)
        service.set_required_fields(phone.id, ("phone",)); service.set_required_fields(insurer.id, ("phone", "insurer_name"))
        self.assertTrue(next(item for item in service.evaluate_availability(phone.id).items if item.code == "company").passed)
        self.assertFalse(next(item for item in service.evaluate_availability(insurer.id).items if item.code == "company").passed)

    def test_intervention_zero_regime_can_become_available_and_converter_failure_is_capability_only(self):
        sheet = self.service.add_model("Fiche disponible", "INTERVENTION_SHEET", "1", self.sheet_source)
        self.service.set_context_review(sheet.id, ReviewEvidenceStatus.NOT_APPLICABLE)
        for code in EXTERNAL_GATE_CODES: self.service.set_external_gate(sheet.id, code, ExternalGateStatus.NOT_APPLICABLE, "N/A explicite")
        self.service.set_external_content(sheet.id, ExternalGateStatus.CONFIRMED, "Validateur", "2026-08-17", "Fiche", "REF")
        self.assertTrue(self.service.control_structure(sheet.id).passed); self.service.test_generation(sheet.id)
        self.service.set_visual_review(sheet.id, ReviewEvidenceStatus.NOT_APPLICABLE)
        self.assertTrue(self.service.evaluate_availability(sheet.id).ready)
        self.assertIs(self.service.make_available(sheet.id).status, TemplateVersionStatus.AVAILABLE)
        self.assertIn(sheet.id, [item.id for item in self.service.list_available_intervention_sheets()])

        blocked = self.add_contract("Poste indisponible", "1"); self.converter.enabled = False
        before = self.service.validation_record(blocked.id)
        with self.assertRaises(DocumentGenerationError): self.service.test_generation(blocked.id)
        after = self.service.validation_record(blocked.id)
        self.assertEqual((after.render_status, self.service.get_version(blocked.id).status), (before.render_status, TemplateVersionStatus.TO_VALIDATE))

    def test_structure_control_rejects_unknown_placeholder_and_contract_source_as_sheet(self):
        parts = read_package(self.contract_source)
        parts["word/document.xml"] = parts["word/document.xml"].replace(b"company.display_name", b"company.unknown_s12")
        unknown = self.root / "unknown.docx"; write_package(parts, unknown)
        version = self.service.add_model("Placeholder inconnu", "CONTRACT", "1", unknown)
        result = self.service.control_structure(version.id)
        self.assertFalse(result.passed); self.assertIn("Placeholder inconnu", result.issues)
        wrong = self.service.add_model("Mauvais type", "INTERVENTION_SHEET", "1", self.contract_source)
        wrong_result = self.service.control_structure(wrong.id)
        self.assertFalse(wrong_result.passed); self.assertTrue(any("Bloc" in issue or "Structure" in issue for issue in wrong_result.issues))

    def test_non_official_1_10_30_validation_and_explicit_availability_have_no_business_rows(self):
        version = self.add_contract(); render = self.make_ready(version.id)
        self.assertEqual(render.equipment_coverage, (1, 10, 30))
        self.assertTrue(all(path.startswith(f"validation/models/{version.id}/") for path in render.evidence_paths))
        self.assertTrue(self.service.evaluate_availability(version.id).ready)
        with self.database.connection() as connection:
            before = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("contracts", "contract_documents", "contract_events"))
        available = self.service.make_available(version.id); self.assertIs(available.status, TemplateVersionStatus.AVAILABLE)
        with self.database.connection() as connection:
            after = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("contracts", "contract_documents", "contract_events"))
        self.assertEqual(after, before); self.assertFalse(any("documents/contracts" in path for path in render.evidence_paths))
        with self.assertRaises(Exception): self.service.set_target_regimes(version.id, ("PROFESSIONAL",))

    def test_available_status_is_not_demoted_by_source_or_company_regression_and_archive_is_terminal(self):
        version = self.add_contract(); self.make_ready(version.id); self.service.make_available(version.id)
        source = self.store.verify(version.source_relpath, version.source_hash); source.unlink()
        self.assertEqual(self.service.source_integrity(version.id)[0], "MISSING")
        self.assertIs(self.service.get_version(version.id).status, TemplateVersionStatus.AVAILABLE)
        restored = self.service.restore_source(version.id, self.contract_source); self.assertTrue(restored.is_file())
        archived = self.service.archive(version.id); self.assertIs(archived.status, TemplateVersionStatus.ARCHIVED)
        self.assertNotIn(version.id, [item.id for item in self.service.list_compatible("CLIMATE_MAINTENANCE", "CONSUMER")])
        with self.assertRaises(Exception): self.service.make_available(version.id)

    def test_source_restore_requires_historical_hash(self):
        version = self.add_contract(); controlled = self.store.verify(version.source_relpath, version.source_hash); controlled.unlink()
        with self.assertRaises(Exception): self.service.restore_source(version.id, self.sheet_source)
        self.assertFalse(controlled.exists()); self.assertEqual(self.service.get_version(version.id).source_hash, version.source_hash)
        self.service.restore_source(version.id, self.contract_source); self.assertEqual(hashlib.sha256(controlled.read_bytes()).hexdigest(), version.source_hash)

    def test_models_qt_surface_has_exact_table_states_distinct_flows_and_no_delete(self):
        pending = self.add_contract("À contrôler", "1")
        ready = self.add_contract("Disponible synthétique", "1"); self.make_ready(ready.id); self.service.make_available(ready.id)
        archived = self.add_contract("Archivé synthétique", "1"); self.service.archive(archived.id)
        page = ModelsSettingsPage(self.service)
        self.assertEqual(tuple(page.table.horizontalHeaderItem(index).text() for index in range(page.table.columnCount())), ModelsSettingsPage.COLUMNS)
        states = {page.table.item(row, 3).text() for row in range(page.table.rowCount())}; self.assertEqual(states, {"À valider", "Disponible", "Archivé"})
        page.resize(540, 700); self.application.processEvents()
        self.assertFalse(page.table.horizontalScrollBar().isVisible())
        self.assertGreaterEqual(page.table.columnWidth(0), 90); self.assertGreaterEqual(page.table.columnWidth(1), 90)
        self.assertTrue(any(button.text() == "Ajouter un modèle" for button in page.findChildren(QPushButton)))
        self.assertTrue(any(button.text() == "Nouvelle version" for button in page.findChildren(QPushButton)))
        self.assertFalse(any(button.text().lower() in {"supprimer", "delete"} for button in page.findChildren(QPushButton)))
        add = AddModelDialog(); new = NewVersionDialog("Famille"); self.assertNotEqual(add.objectName(), new.objectName()); add.close(); new.close()
        page.open_version(pending.id)
        labels = [label.text() for label in page.findChildren(QLabel)]
        for title in ("Identité & source", "Utilisation / régimes", "Données requises", "Blocs de contexte", "Tarification", "Contrôle de structure", "Test de génération", "Validation externe", "Conditions de mise à disposition"):
            self.assertIn(title, labels)
        page.close()

    def test_settings_models_section_is_functional_and_empty_state_is_truthful(self):
        company_repo = CompanySettingsRepository(self.database)
        company = CompanySettingsService(company_repo, self.repository, self.workspace.root)
        view = CompanySettingsView(company, WorkspaceService(), self.service)
        view._show_section("Modèles")
        self.assertIsInstance(view.stack.currentWidget(), ModelsSettingsPage)
        self.assertEqual(view.stack.currentWidget().empty.text(), "Aucun modèle configuré")
        view.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
