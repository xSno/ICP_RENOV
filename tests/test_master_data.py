from __future__ import annotations

import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from icp_renov_contracts.app import create_application
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.domain import ClientDraft, EquipmentDraft, SiteDraft
from icp_renov_contracts.errors import MasterDataValidationError
from icp_renov_contracts.ui import MainWindow, NAVIGATION_LABELS
from icp_renov_contracts.ui.clients_view import ClientsInstallationsView
from icp_renov_contracts.ui.master_forms import ClientEditor, EquipmentEditor, SiteEditor

from test_foundation import scratch


def person(first_name: str = "Camille", last_name: str = "Exemple", **overrides) -> ClientDraft:
    values = dict(
        party_type="PERSON", first_name=first_name, last_name=last_name,
        address_line1="20 avenue du Test", postal_code="75000", city="Paris",
        phone="01 02 03 04 05", email="camille@example.invalid",
    )
    values.update(overrides)
    return ClientDraft(**values)


def organization(name: str = "Résidence Le Levant", **overrides) -> ClientDraft:
    values = dict(
        party_type="ORGANIZATION", organization_name=name, legal_form="Syndicat de copropriété",
        siret="00000000000000", address_line1="1 rue du Levant", postal_code="69000", city="Lyon",
        billing_address="2 rue de la Gestion, 69000 Lyon", proposed_contact_name="Alex Martin",
        proposed_contact_role="Gestionnaire", internal_reference="CLI-LEVANT",
    )
    values.update(overrides)
    return ClientDraft(**values)


def site(label: str, city: str = "Lyon", **overrides) -> SiteDraft:
    values = dict(label=label, address_line1=f"10 rue {label}", postal_code="69000", city=city)
    values.update(overrides)
    return SiteDraft(**values)


def equipment(kind: str, location: str, **overrides) -> EquipmentDraft:
    values = dict(equipment_type=kind, location=location)
    values.update(overrides)
    return EquipmentDraft(**values)


class MasterDataCase(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch()
        self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.service = self.context.master_data

    def tearDown(self):
        self._scratch.__exit__(None, None, None)


class MigrationTests(unittest.TestCase):
    def test_migration_one_to_s1_succeeds_without_rewriting_one(self):
        with scratch() as temporary:
            path = Path(temporary) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_migrations VALUES (1, 'frozen-migration-1')")
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            database = DatabaseService(path)
            database.initialize()
            self.assertEqual(database.schema_version(), 12)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT applied_at_utc FROM schema_migrations WHERE version=1").fetchone()[0], "frozen-migration-1")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"clients", "sites", "equipment"}.issubset(tables))

    def test_s1_migration_reopen_is_idempotent(self):
        with scratch() as temporary:
            database = DatabaseService(Path(temporary) / "database.sqlite3")
            database.initialize()
            database.initialize()
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 12)


class ClientPersistenceTests(MasterDataCase):
    def test_create_person_and_organization_with_optional_fields(self):
        created_person = self.service.create_client(person(internal_notes="Accès matin uniquement"))
        created_org = self.service.create_client(organization())
        self.assertEqual(self.service.get_client(created_person.id).display_name, "Camille Exemple")
        stored = self.service.get_client(created_org.id)
        self.assertEqual(stored.organization_name, "Résidence Le Levant")
        self.assertEqual(stored.proposed_contact_role, "Gestionnaire")
        self.assertEqual(stored.billing_address, "2 rue de la Gestion, 69000 Lyon")

    def test_client_schema_has_no_contract_regime(self):
        with self.context.database.connection() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(clients)")}
            equipment_columns = {row[1] for row in connection.execute("PRAGMA table_info(equipment)")}
        self.assertNotIn("regime", columns)
        self.assertNotIn("client_regime", columns)
        self.assertIn("internal_notes", equipment_columns)
        self.assertNotIn("observations", equipment_columns)

    def test_required_field_validation_and_light_email_validation(self):
        with self.assertRaises(MasterDataValidationError) as error:
            self.service.create_client(person(first_name="", email="invalid"))
        self.assertIn("first_name", error.exception.field_errors)
        self.assertIn("email", error.exception.field_errors)
        with self.assertRaises(MasterDataValidationError):
            self.service.create_client(organization(name=""))

    def test_update_keeps_party_type_stable(self):
        client = self.service.create_client(person())
        changed = person(first_name="Camille-Marie")
        self.assertEqual(self.service.update_client(client.id, changed).first_name, "Camille-Marie")
        with self.assertRaises(MasterDataValidationError):
            self.service.update_client(client.id, organization())

    def test_archive_restore_search_filter_and_counts(self):
        first = self.service.create_client(person("Camille", "Exemple"))
        second = self.service.create_client(organization("Résidence Le Levant"))
        site_a = self.service.create_site(second.id, site("Bâtiment A"))
        site_b = self.service.create_site(second.id, site("Bâtiment B"))
        self.service.create_equipment(site_a.id, equipment("Unité murale", "Hall"))
        self.service.create_equipment(site_b.id, equipment("Groupe extérieur", "Toiture"))
        summary = self.service.list_clients("levant")[0]
        self.assertEqual((summary.site_count, summary.equipment_count), (2, 2))
        self.service.archive_client(first.id)
        self.assertEqual([row.client.id for row in self.service.list_clients(archived=True)], [first.id])
        self.assertNotIn(first.id, [row.client.id for row in self.service.list_clients(archived=False)])
        self.service.restore_client(first.id)
        self.assertIn(first.id, [row.client.id for row in self.service.list_clients(archived=False)])


class RelationshipArchiveTests(MasterDataCase):
    def setUp(self):
        super().setUp()
        self.client = self.service.create_client(organization())
        self.site_a = self.service.create_site(self.client.id, site("Bâtiment A", contact_name="Gardien"))
        self.site_b = self.service.create_site(self.client.id, site("Bâtiment B"))
        self.eq_a = self.service.create_equipment(
            self.site_a.id,
            equipment("Unité murale", "Accueil", brand="Daikin", model="Demo", power_kw=3.5, internal_notes="Donnée maître interne"),
        )
        self.eq_b = self.service.create_equipment(self.site_a.id, equipment("Cassette", "Salle 2"))

    def test_multiple_sites_and_equipment_parent_relationships(self):
        self.assertEqual({item.id for item in self.service.list_sites(self.client.id)}, {self.site_a.id, self.site_b.id})
        equipment_ids = {item.id for item in self.service.list_equipment(self.site_a.id)}
        self.assertEqual(equipment_ids, {self.eq_a.id, self.eq_b.id})
        self.assertEqual(self.service.get_equipment(self.eq_a.id).site_id, self.site_a.id)
        self.assertEqual(self.service.get_site(self.site_a.id).client_id, self.client.id)

    def test_archive_client_does_not_cascade(self):
        self.service.archive_client(self.client.id)
        self.assertTrue(self.service.get_client(self.client.id).archived)
        self.assertFalse(self.service.get_site(self.site_a.id).archived)
        self.assertFalse(self.service.get_equipment(self.eq_a.id).archived)
        self.service.restore_client(self.client.id)
        self.assertFalse(self.service.get_client(self.client.id).archived)

    def test_archive_site_does_not_cascade_and_restore(self):
        self.service.archive_site(self.site_a.id)
        self.assertTrue(self.service.get_site(self.site_a.id).archived)
        self.assertFalse(self.service.get_equipment(self.eq_a.id).archived)
        self.service.restore_site(self.site_a.id)
        self.assertFalse(self.service.get_site(self.site_a.id).archived)

    def test_archive_restore_equipment(self):
        self.service.archive_equipment(self.eq_a.id)
        self.assertTrue(self.service.get_equipment(self.eq_a.id).archived)
        self.service.restore_equipment(self.eq_a.id)
        self.assertFalse(self.service.get_equipment(self.eq_a.id).archived)

    def test_invalid_power_is_rejected(self):
        with self.assertRaises(MasterDataValidationError):
            self.service.create_equipment(self.site_a.id, equipment("Unité", "Hall", power_kw=-1))
        with self.assertRaises(MasterDataValidationError):
            self.service.create_equipment(self.site_a.id, equipment("Unité", "Hall", power_kw="abc"))

    def test_foreign_keys_restrict_physical_parent_delete(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.context.database.transaction() as connection:
                connection.execute("DELETE FROM clients WHERE id = ?", (self.client.id,))
        self.assertEqual(self.service.get_site(self.site_a.id).client_id, self.client.id)

    def test_service_exposes_no_delete_operation(self):
        names = {name for name in dir(self.service) if not name.startswith("_")}
        self.assertFalse(any("delete" in name.lower() or "supprim" in name.lower() for name in names))


class MasterDataUiTests(MasterDataCase):
    @classmethod
    def setUpClass(cls):
        cls.application = create_application(["test-master-data"])

    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.context)
        self.window.show()
        self.window.shell.navigate("Clients & installations")
        self.application.processEvents()
        self.view = self.window.shell.surface("Clients & installations")

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def test_surface_replaces_placeholder_and_navigation_is_unchanged(self):
        self.assertIsInstance(self.view, ClientsInstallationsView)
        self.assertEqual(self.window.shell.navigation_labels, NAVIGATION_LABELS)
        self.window.shell.navigate("Clients & installations")
        self.assertEqual(self.window.shell.current_surface, "Clients & installations")

    def test_empty_workspace_state(self):
        self.assertFalse(self.view.list_empty.isHidden())
        self.assertEqual(self.view.client_list.count(), 0)

    def test_persisted_client_selection_and_detail(self):
        client = self.service.create_client(person())
        self.view.refresh_clients(client.id)
        self.assertEqual(self.view.client_list.count(), 1)
        self.assertEqual(self.view.selected_client_id, client.id)
        text = " ".join(label.text() for label in self.view.detail.findChildren(QLabel))
        self.assertIn("Camille Exemple", text)
        self.assertNotIn("Régime", text)

    def test_multi_site_and_equipment_render_under_correct_site(self):
        client = self.service.create_client(organization())
        first = self.service.create_site(client.id, site("Bâtiment A"))
        second = self.service.create_site(client.id, site("Bâtiment B"))
        equipment_a = self.service.create_equipment(first.id, equipment("Unité murale", "Hall A"))
        equipment_b = self.service.create_equipment(second.id, equipment("Cassette", "Hall B"))
        self.view.refresh_clients(client.id)
        cards = self.view.detail.findChildren(QWidget, "siteCard")
        self.assertEqual(len(cards), 2)
        by_id = {card.property("siteId"): " ".join(label.text() for label in card.findChildren(QLabel)) for card in cards}
        self.assertIn("Hall A", by_id[first.id])
        self.assertNotIn("Hall B", by_id[first.id])
        self.assertIn("Hall B", by_id[second.id])

    def test_active_archived_filter_changes_visible_set(self):
        active = self.service.create_client(person())
        archived = self.service.create_client(organization())
        self.service.archive_client(archived.id)
        self.view.refresh_clients()
        self.assertEqual(self.view.client_list.item(0).data(Qt.ItemDataRole.UserRole), active.id)
        self.view.filter.setCurrentIndex(1)
        self.assertEqual(self.view.client_list.count(), 1)
        self.assertEqual(self.view.client_list.item(0).data(Qt.ItemDataRole.UserRole), archived.id)

    def test_client_form_has_no_regime_and_ui_has_no_delete_action(self):
        editor = ClientEditor(parent=self.window)
        all_text = " ".join(
            [widget.text() for widget in editor.findChildren(QLabel)]
            + [widget.text() for widget in editor.findChildren(QPushButton)]
        )
        self.assertNotIn("Régime", all_text)
        editor.close()
        client = self.service.create_client(person())
        self.view.refresh_clients(client.id)
        buttons = self.window.findChildren(QPushButton)
        self.assertFalse(any("Supprimer" in button.text() or "Delete" in button.text() for button in buttons))

    def test_new_client_uses_same_primary_treatment_as_add_site(self):
        client = self.service.create_client(person())
        self.view.refresh_clients(client.id)
        add_site = next(
            button for button in self.view.detail.findChildren(QPushButton)
            if button.text() == "Ajouter un site"
        )
        self.assertEqual(self.view.new_client_button.objectName(), "primaryButton")
        self.assertEqual(add_site.objectName(), "primaryButton")

    def test_client_editor_save_persists_refreshes_and_selects(self):
        self.view.open_new_client()
        editor = self.view.active_editor
        editor.first_name.setText("Jeanne")
        editor.last_name.setText("Durand")
        editor.address_line1.setText("4 rue Locale")
        editor.postal_code.setText("75000")
        editor.city.setText("Paris")
        editor.save_button.click()
        self.assertEqual(self.view.client_list.count(), 1)
        self.assertEqual(self.service.get_client(self.view.selected_client_id).display_name, "Jeanne Durand")

    def assert_in_app_drawer(self, editor, expected_type, title):
        self.assertIsInstance(editor, expected_type)
        self.assertEqual(editor.editor_title, title)
        self.assertIs(editor.parentWidget(), self.view.drawer_host)
        self.assertFalse(editor.isWindow())
        self.assertIs(editor.window(), self.window)
        self.assertEqual(editor.save_button.text(), "Enregistrer")
        self.assertEqual(editor.cancel_button.text(), "Annuler")
        self.assertFalse(self.view.drawer_host.isHidden())

    def test_all_create_and_edit_flows_use_same_in_app_drawer(self):
        client = self.service.create_client(person())
        created_site = self.service.create_site(client.id, site("Site test"))
        created_equipment = self.service.create_equipment(
            created_site.id, equipment("Unité murale", "Hall")
        )
        cases = (
            (self.view.open_new_client, (), ClientEditor, "Nouveau client"),
            (self.view.open_edit_client, (client,), ClientEditor, "Modifier le client"),
            (self.view.open_new_site, (client.id,), SiteEditor, "Ajouter un site"),
            (self.view.open_edit_site, (created_site,), SiteEditor, "Modifier le site"),
            (self.view.open_new_equipment, (created_site.id,), EquipmentEditor, "Ajouter un équipement"),
            (self.view.open_edit_equipment, (created_equipment,), EquipmentEditor, "Modifier l’équipement"),
        )
        for opener, arguments, editor_type, title in cases:
            with self.subTest(title=title):
                opener(*arguments)
                self.assert_in_app_drawer(self.view.active_editor, editor_type, title)
                self.view.active_editor.cancel_button.click()
                self.application.processEvents()
                self.assertIsNone(self.view.active_editor)
                self.assertTrue(self.view.drawer_host.isHidden())

    def test_validation_keeps_entered_values_in_open_drawer(self):
        self.view.open_new_client()
        editor = self.view.active_editor
        editor.first_name.setText("Valeur conservée")
        editor.save_button.click()
        self.assertIs(self.view.active_editor, editor)
        self.assertEqual(editor.first_name.text(), "Valeur conservée")
        self.assertFalse(editor.error_label.isHidden())

    def test_escape_closes_drawer_without_changing_persisted_data(self):
        client = self.service.create_client(person())
        self.view.open_edit_client(client)
        editor = self.view.active_editor
        editor.first_name.setText("Modification non enregistrée")
        QTest.keyClick(editor.first_name, Qt.Key.Key_Escape)
        self.application.processEvents()
        self.assertIsNone(self.view.active_editor)
        self.assertTrue(self.view.drawer_host.isHidden())
        self.assertEqual(self.service.get_client(client.id).first_name, "Camille")


if __name__ == "__main__":
    unittest.main(verbosity=2)
