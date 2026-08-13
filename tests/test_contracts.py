from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton

from icp_renov_contracts.app import create_application
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.database.service import MIGRATIONS
from icp_renov_contracts.domain import ClientDraft, EquipmentDraft, SiteDraft
from icp_renov_contracts.errors import ContractPersistenceError, ContractValidationError
from icp_renov_contracts.ui import MainWindow
from icp_renov_contracts.ui.contracts_view import ContractsView, ObservationEditor, SelectionPanel
from icp_renov_contracts.ui.master_forms import ClientEditor, EquipmentEditor, SiteEditor

from test_foundation import scratch
from test_master_data import equipment, organization, person, site


class ContractCase(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data; self.contracts = self.context.contracts
        self.client = self.master.create_client(organization())
        self.site_a = self.master.create_site(self.client.id, site("Bâtiment A", internal_notes="code portail"))
        self.site_b = self.master.create_site(self.client.id, site("Bâtiment B"))
        self.eq_a = self.master.create_equipment(self.site_a.id, equipment(
            "Unité murale", "Accueil", brand="Daikin", model="FTX", power_kw=3.5,
            installation_date="2024-01-02", internal_notes="Secret maître",
        ))
        self.eq_b = self.master.create_equipment(self.site_a.id, equipment("Cassette", "Salle 2"))

    def tearDown(self):
        self._scratch.__exit__(None, None, None)

    def populated(self):
        draft = self.contracts.create_draft()
        self.contracts.select_client(draft.id, self.client.id)
        self.contracts.select_site(draft.id, self.site_a.id)
        self.contracts.select_equipment(draft.id, self.eq_a.id)
        return self.contracts.get(draft.id)


class ContractMigrationTests(unittest.TestCase):
    def test_migration_two_to_three_and_reopen_are_idempotent(self):
        with scratch() as temporary:
            path = Path(temporary) / "s1.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:2]:
                    for statement in migration.statements: connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations VALUES (?,?)", (migration.version, "frozen"))
                connection.execute("PRAGMA user_version=2"); connection.commit()
            database = DatabaseService(path); database.initialize(); database.initialize()
            self.assertEqual(database.schema_version(), 3)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 3)
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"contracts", "contract_equipment_items"}.issubset(tables))


class DraftAndSnapshotTests(ContractCase):
    def test_empty_draft_reopens_after_new_context_without_number_or_revision(self):
        draft = self.contracts.create_draft()
        self.assertEqual(draft.status.value, "DRAFT"); self.assertIsNone(draft.client_snapshot)
        with self.context.database.connection() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(contracts)")}
        self.assertIn("regime", columns); self.assertFalse({"number", "revision", "official_number"} & columns)
        rebuilt = build_application_context(config_store=self.context.config_store)
        self.assertEqual(rebuilt.contracts.get(draft.id).id, draft.id)

    def test_regime_nullable_on_contract_only_and_client_has_none(self):
        draft = self.contracts.create_draft()
        self.assertIsNone(draft.regime)
        with self.context.database.connection() as connection:
            client_columns = {row[1] for row in connection.execute("PRAGMA table_info(clients)")}
        self.assertNotIn("regime", client_columns)

    def test_client_snapshot_and_signatory_do_not_follow_master_edit(self):
        draft = self.contracts.create_draft(); stored = self.contracts.select_client(draft.id, self.client.id)
        self.assertEqual((stored.signatory_name, stored.signatory_role), ("Alex Martin", "Gestionnaire"))
        changed = organization("Résidence Modifiée", proposed_contact_name="Autre", proposed_contact_role="Président")
        self.master.update_client(self.client.id, changed)
        reopened = self.contracts.get(draft.id)
        self.assertEqual(reopened.client_snapshot.display_name, "Résidence Le Levant")
        self.assertEqual(reopened.signatory_name, "Alex Martin")

    def test_person_signatory_prefill_and_blank_organization_prefill(self):
        individual = self.master.create_client(person("Lina", "Durand"))
        first = self.contracts.create_draft(); first = self.contracts.select_client(first.id, individual.id)
        self.assertEqual((first.signatory_name, first.signatory_role), ("Lina Durand", "Client"))
        org = self.master.create_client(organization("Sans contact", proposed_contact_name="", proposed_contact_role=""))
        second = self.contracts.create_draft(); second = self.contracts.select_client(second.id, org.id)
        self.assertEqual((second.signatory_name, second.signatory_role), ("", ""))

    def test_site_and_equipment_snapshots_do_not_follow_master_edits_or_internal_notes(self):
        draft = self.populated()
        self.master.update_site(self.site_a.id, site("Site renommé", internal_notes="nouveau secret"))
        self.master.update_equipment(self.eq_a.id, equipment("Pompe", "Toit", brand="Autre", internal_notes="Très secret"))
        reopened = self.contracts.get(draft.id)
        self.assertEqual(reopened.site_snapshot.label, "Bâtiment A")
        self.assertEqual(reopened.equipment_items[0].snapshot.equipment_type, "Unité murale")
        self.assertNotIn("internal_notes", reopened.equipment_items[0].snapshot.__dataclass_fields__)
        self.assertNotIn("Secret", reopened.equipment_items[0].snapshot.to_json())

    def test_contract_signatory_and_observation_never_mutate_master(self):
        draft = self.populated(); item = draft.equipment_items[0]
        self.contracts.update_signatory(draft.id, "Signataire contrat", "Mandataire")
        self.contracts.update_observation(draft.id, item.id, "Prévoir protection du sol")
        self.assertEqual(self.master.get_client(self.client.id).proposed_contact_name, "Alex Martin")
        self.assertEqual(self.master.get_equipment(self.eq_a.id).internal_notes, "Secret maître")

    def test_archived_sources_remain_snapshotted_but_are_not_selectable(self):
        draft = self.populated(); self.master.archive_client(self.client.id); self.master.archive_equipment(self.eq_a.id)
        reopened = self.contracts.get(draft.id)
        self.assertEqual(reopened.client_snapshot.display_name, "Résidence Le Levant")
        self.assertEqual(len(reopened.equipment_items), 1)
        self.assertEqual(self.contracts.list_drafts()[0].client_name, "Résidence Le Levant")
        self.assertNotIn(self.client.id, [row.client.id for row in self.contracts.selectable_clients()])
        other = self.contracts.create_draft()
        with self.assertRaises(ContractValidationError): self.contracts.select_client(other.id, self.client.id)


class CascadeAndEquipmentTests(ContractCase):
    def test_client_cascade_rolls_back_as_one_transaction_on_failure(self):
        draft = self.populated(); other = self.master.create_client(person("Nora", "Martin"))
        with self.context.database.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER reject_contract_item_delete BEFORE DELETE ON contract_equipment_items "
                "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
            )
        with self.assertRaises(ContractPersistenceError): self.contracts.select_client(draft.id, other.id)
        unchanged = self.contracts.get(draft.id)
        self.assertEqual(unchanged.client_source_id, self.client.id)
        self.assertEqual(unchanged.site_source_id, self.site_a.id)
        self.assertEqual(len(unchanged.equipment_items), 1)

    def test_change_client_clears_site_equipment_observation_regime_and_reprefs_signatory(self):
        draft = self.populated(); item = draft.equipment_items[0]
        self.contracts.update_observation(draft.id, item.id, "Contrat seulement")
        with self.context.database.transaction() as connection:
            connection.execute("UPDATE contracts SET regime='PROFESSIONAL' WHERE id=?", (draft.id,))
        other = self.master.create_client(person("Nora", "Martin"))
        changed = self.contracts.select_client(draft.id, other.id)
        self.assertIsNone(changed.site_snapshot); self.assertEqual(changed.equipment_items, ())
        self.assertIsNone(changed.regime); self.assertEqual((changed.signatory_name, changed.signatory_role), ("Nora Martin", "Client"))
        self.assertEqual(self.master.get_equipment(self.eq_a.id).internal_notes, "Secret maître")

    def test_change_site_clears_equipment_and_master_is_untouched(self):
        draft = self.populated(); self.contracts.update_observation(draft.id, draft.equipment_items[0].id, "Note")
        changed = self.contracts.select_site(draft.id, self.site_b.id)
        self.assertEqual(changed.equipment_items, ()); self.assertEqual(self.master.get_equipment(self.eq_a.id).site_id, self.site_a.id)

    def test_collection_append_deselect_compact_move_and_reopen(self):
        draft = self.populated(); draft = self.contracts.select_equipment(draft.id, self.eq_b.id)
        self.assertEqual([i.source_equipment_id for i in draft.equipment_items], [self.eq_a.id, self.eq_b.id])
        draft = self.contracts.move_equipment(draft.id, draft.equipment_items[1].id, -1)
        self.assertEqual([i.source_equipment_id for i in draft.equipment_items], [self.eq_b.id, self.eq_a.id])
        rebuilt = build_application_context(config_store=self.context.config_store)
        self.assertEqual([i.position for i in rebuilt.contracts.get(draft.id).equipment_items], [0, 1])
        draft = self.contracts.update_observation(draft.id, draft.equipment_items[0].id, "à retirer")
        draft = self.contracts.deselect_equipment(draft.id, self.eq_b.id)
        self.assertEqual([(i.position, i.source_equipment_id) for i in draft.equipment_items], [(0, self.eq_a.id)])

    def test_reject_equipment_from_other_site_and_archived_new_selection(self):
        draft = self.populated(); foreign = self.master.create_equipment(self.site_b.id, equipment("Groupe", "Toit"))
        with self.assertRaises(ContractValidationError): self.contracts.select_equipment(draft.id, foreign.id)
        self.master.archive_equipment(self.eq_b.id)
        with self.assertRaises(ContractValidationError): self.contracts.select_equipment(draft.id, self.eq_b.id)

    def test_new_equipment_from_contract_is_selected_last(self):
        draft = self.populated()
        draft = self.contracts.create_and_select_equipment(draft.id, equipment("Gainable", "Combles"))
        self.assertEqual(len(draft.equipment_items), 2)
        self.assertEqual(draft.equipment_items[-1].snapshot.location, "Combles")


class ContractUiTests(ContractCase):
    @classmethod
    def setUpClass(cls): cls.application = create_application(["test-contracts"])

    def setUp(self):
        super().setUp(); self.window = MainWindow(self.context); self.window.show(); self.application.processEvents()
        self.view: ContractsView = self.window.shell.surface("Contrats")

    def tearDown(self):
        self.window.close(); self.application.processEvents(); super().tearDown()

    def test_landing_creates_persisted_draft_and_workspace_has_only_first_step_enabled(self):
        self.assertIsInstance(self.view, ContractsView); self.assertEqual(self.view.draft_list.count(), 0)
        QTest.mouseClick(self.view.new_contract_button, Qt.MouseButton.LeftButton); self.application.processEvents()
        self.assertEqual(len(self.contracts.list_drafts()), 1); self.assertEqual(self.view.number_label.text(), "Brouillon sans numéro")
        self.assertEqual(
            tuple(button.text().split(". ", 1)[1].replace("&&", "&") for button in self.view.step_buttons),
            self.view.STEP_LABELS,
        )
        self.assertEqual(self.view.step_buttons[0].text(), "1. Client, site && équipements")
        self.assertEqual(self.view.step_buttons[3].text(), "4. Documents && suivi")
        self.assertEqual([button.isEnabled() for button in self.view.step_buttons], [True, False, False, False])
        all_text = " ".join(widget.text() for widget in self.view.findChildren(QPushButton))
        for forbidden in ("Générer", "Supprimer", "Régime", "Conclusion", "Modèle"):
            self.assertNotIn(forbidden, all_text)

    def test_separate_in_app_selectors_and_master_editors(self):
        draft = self.contracts.create_draft(); self.view.open_contract(draft.id); self.view.open_client_selector()
        self.assertIsInstance(self.view.active_drawer, SelectionPanel); self.assertIs(self.view.active_drawer.window(), self.window)
        panel = self.view.active_drawer; panel.list.itemClicked.emit(panel.list.item(0)); self.application.processEvents()
        self.assertEqual(self.contracts.get(draft.id).client_source_id, self.client.id)
        self.view.open_site_selector(); panel = self.view.active_drawer; panel.list.itemClicked.emit(panel.list.item(0)); self.application.processEvents()
        self.assertEqual(self.contracts.get(draft.id).site_source_id, self.site_a.id)
        self.view.open_new_client(); self.assertIsInstance(self.view.active_drawer, ClientEditor)
        self.assertFalse(any(isinstance(widget, QDialog) for widget in self.window.findChildren(QDialog)))
        self.view.active_drawer.escape_shortcut.activated.emit(); self.application.processEvents(); self.assertIsNone(self.view.active_drawer)
        self.view.open_new_site()
        self.assertIsInstance(self.view.active_drawer, SiteEditor); self.assertIs(self.view.active_drawer.window(), self.window)
        self.view.open_new_equipment()
        self.assertIsInstance(self.view.active_drawer, EquipmentEditor); self.assertIs(self.view.active_drawer.window(), self.window)

    def test_equipment_checkbox_zero_notice_order_and_observation_drawer(self):
        draft = self.contracts.create_draft(); self.contracts.select_client(draft.id, self.client.id); self.contracts.select_site(draft.id, self.site_a.id)
        self.view.open_contract(draft.id)
        self.assertTrue(any(label.text() == "Aucun équipement sélectionné" for label in self.view.findChildren(type(self.view.draft_empty))))
        check = next(box for box in self.view.findChildren(QCheckBox) if self.eq_a.display_name in box.text())
        QTest.mouseClick(check, Qt.MouseButton.LeftButton); self.application.processEvents()
        stored = self.contracts.get(draft.id); self.assertEqual(len(stored.equipment_items), 1)
        self.view.open_observation(stored.equipment_items[0].id)
        self.assertIsInstance(self.view.active_drawer, ObservationEditor); self.assertIs(self.view.active_drawer.window(), self.window)
        self.assertFalse(hasattr(self.view.active_drawer, "regime"))

    def test_existing_equipment_row_uses_snapshot_after_master_edit_and_archive(self):
        draft = self.populated()
        self.master.update_equipment(self.eq_a.id, equipment("Pompe modifiée", "Toit", brand="Autre"))
        self.master.archive_equipment(self.eq_a.id)
        self.view.open_contract(draft.id)
        labels = [box.text() for box in self.view.findChildren(QCheckBox)]
        self.assertIn("Unité murale — Daikin — FTX", labels)
        self.assertNotIn("Pompe modifiée — Autre", labels)
