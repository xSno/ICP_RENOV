from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit

from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.documents import PersistedCompanyDocumentDataProvider
from icp_renov_contracts.repositories import CompanySettingsRepository, TemplateCatalogRepository
from icp_renov_contracts.services import CompanySettingsService
from icp_renov_contracts.services import TemplateCatalogService
from icp_renov_contracts.storage import WorkspaceService
from icp_renov_contracts.ui.settings_view import CompanySettingsView
from icp_renov_contracts.domain import TemplateVersionStatus


class CompanySettingsS11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(["company-settings-s11"])

    def setUp(self):
        test_tmp = Path(__file__).parent / "tmp"; test_tmp.mkdir(parents=True, exist_ok=True)
        self.root = test_tmp / uuid.uuid4().hex; self.root.mkdir()
        self.workspace = WorkspaceService().ensure(self.root / "workspace")
        self.database = DatabaseService(self.workspace.database_path); self.database.initialize()
        self.repository = CompanySettingsRepository(self.database)
        self.service = CompanySettingsService(self.repository, TemplateCatalogRepository(self.database), self.workspace.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_migration_ten_creates_one_defaulted_singleton(self):
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM company_settings").fetchone()[0], 1)
        settings = self.service.get()
        self.assertEqual(settings.trade_name, "ICP Renov")
        self.assertEqual(settings.address_line1, "1138 boulevard Jean Moulin")
        self.assertEqual((settings.postal_code, settings.city, settings.country), ("83700", "Saint-Raphaël", "France"))
        self.assertEqual((settings.phone, settings.email, settings.siret, settings.ape_code), ("06 27 47 33 94", "icprenov83@gmail.com", "98948879600016", "43.22A"))
        self.assertEqual(settings.legal_name, "")

    def test_partial_profile_is_valid_and_identifiers_are_normalized(self):
        saved = self.service.save(replace(self.service.get(), legal_name="", share_capital="1 250,50", siren="123 456 789", siret="123 456 789 00010", ape_code="43.22a"))
        self.assertEqual((saved.siren, saved.siret, saved.ape_code, saved.share_capital), ("123456789", "12345678900010", "43.22A", Decimal("1250.50")))
        before = self.service.get()
        with self.assertRaises(ValueError): self.service.save(replace(before, siret="not-a-siret"))
        self.assertEqual(self.service.get(), before)

    def test_logo_is_copied_to_controlled_workspace_resource_and_verified(self):
        source = self.root / "source.png"; data = b"\x89PNG\r\n\x1a\nsynthetic"; source.write_bytes(data)
        saved = self.service.import_logo(source)
        self.assertTrue(saved.logo_relpath.startswith("settings/company/logo/"))
        controlled = self.workspace.root / saved.logo_relpath
        self.assertTrue(controlled.is_file()); self.assertEqual(saved.logo_hash, hashlib.sha256(data).hexdigest()); self.assertTrue(self.service.logo_integrity())
        controlled.write_bytes(b"changed"); self.assertFalse(self.service.logo_integrity())
        self.service.remove_logo(); self.assertIsNone(self.service.get().logo_relpath); self.assertFalse(controlled.exists())

    def test_jpeg_logo_is_accepted_as_a_controlled_resource(self):
        source = self.root / "source.jpg"; source.write_bytes(b"\xff\xd8\xffsynthetic")
        saved = self.service.import_logo(source)
        self.assertTrue(saved.logo_relpath.endswith(".jpg")); self.assertTrue(self.service.logo_integrity())

    def test_logo_removal_is_exposed_only_when_logo_metadata_is_configured(self):
        view = CompanySettingsView(self.service, WorkspaceService())
        self.assertEqual(view.logo_status.text(), "Aucun logo enregistr\u00e9.")
        self.assertFalse(view.import_logo_button.isHidden())
        self.assertTrue(view.import_logo_button.isEnabled())
        self.assertTrue(view.remove_logo_button.isHidden())
        self.assertFalse(view.remove_logo_button.isEnabled())

        source = self.root / "configured.png"; source.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        view.current = self.service.import_logo(source); view._set_logo_status()
        self.assertEqual(view.logo_status.text(), "Logo enregistr\u00e9 et int\u00e8gre.")
        self.assertFalse(view.remove_logo_button.isHidden())
        self.assertTrue(view.remove_logo_button.isEnabled())

        (self.workspace.root / view.current.logo_relpath).write_bytes(b"changed")
        view._set_logo_status()
        self.assertEqual(view.logo_status.text(), "Le logo enregistr\u00e9 est introuvable ou alt\u00e9r\u00e9.")
        self.assertFalse(view.remove_logo_button.isHidden())
        self.assertTrue(view.remove_logo_button.isEnabled())
        view.close()

    def test_provider_reads_persisted_values_without_document_fallbacks(self):
        self.service.save(replace(self.service.get(), legal_name="ICP RENOV SAS", signatory_name="Camille", signatory_role="Gérante"))
        provider = PersistedCompanyDocumentDataProvider(self.repository, self.workspace.root)
        data = provider.get(); immutable_input = dict(data)
        self.service.save(replace(self.service.get(), legal_name="ICP RENOV FUTUR"))
        self.assertTrue(provider.available())
        self.assertEqual(immutable_input["legal_name"], "ICP RENOV SAS")
        self.assertEqual(provider.get()["legal_name"], "ICP RENOV FUTUR")
        self.assertEqual(data["signatory_name"], "Camille")
        self.assertEqual(data["logo"], "")

    def test_settings_surface_has_exact_internal_sections_and_unknown_placeholder(self):
        view = CompanySettingsView(self.service, WorkspaceService())
        expected_sections = (
            "Soci\u00e9t\u00e9", "Mod\u00e8les", "Num\u00e9rotation & alertes",
            "Stockage & sauvegarde", "Diagnostic g\u00e9n\u00e9ration",
        )
        self.assertEqual(CompanySettingsView.SECTION_LABELS, expected_sections)
        self.assertEqual(tuple(view._section_buttons), expected_sections)
        self.assertEqual(view._section_buttons["Num\u00e9rotation & alertes"].text(), "Num\u00e9rotation && alertes")
        self.assertEqual(view._section_buttons["Stockage & sauvegarde"].text(), "Stockage && sauvegarde")
        self.assertEqual(
            tuple(button.text().replace("&&", "&") for button in view._section_buttons.values()),
            expected_sections,
        )
        self.assertEqual(view.findChild(QLineEdit, "company_legal_name").placeholderText(), "À compléter")
        self.assertEqual(view.stack.count(), 5)
        groups = view.findChildren(QFrame, "companyGroup")
        self.assertEqual(len(groups), 5)
        self.assertEqual([group.findChild(QLabel, "sectionTitle").text() for group in groups], [
            "Identité", "Contacts / signataire", "Assurance", "Fluides frigorigènes", "Informations consommateur",
        ])
        self.assertIn("Code APE / NAF", [label.text() for label in view.findChildren(QLabel)])
        view.close()

    def test_required_by_models_indicator_uses_non_archived_template_metadata_only(self):
        catalog = TemplateCatalogService(TemplateCatalogRepository(self.database))
        template = catalog.create_template("Synthétique S11")
        version = catalog.create_version(template, "S11", TemplateVersionStatus.TO_VALIDATE, ("CONSUMER",))
        catalog.set_generation_metadata(version.id, "", "", ("signatory_name",))
        view = CompanySettingsView(self.service, WorkspaceService())
        self.assertTrue(any("Requis par les modèles utilisés" in label.text() for label in view.findChildren(QLabel)))
        catalog.update_status(version.id, TemplateVersionStatus.ARCHIVED)
        self.assertNotIn("signatory_name", self.service.required_fields())
        view.close()

    def test_save_failure_keeps_input_and_uses_bounded_error(self):
        view = CompanySettingsView(self.service, WorkspaceService())
        field = view.findChild(QLineEdit, "company_legal_name"); field.setText("À réessayer")
        with patch.object(self.service, "save", side_effect=OSError("disk")):
            view._save()
        self.assertEqual(field.text(), "À réessayer")
        self.assertIn("n’ont pas pu être enregistrées", view.feedback.text())
        self.assertNotIn("Informations société enregistrées", view.feedback.text())
        view.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
