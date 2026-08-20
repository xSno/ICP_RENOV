from __future__ import annotations

from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.errors import ContractValidationError
from icp_renov_contracts.ui.web_host import UiBridge

from test_document_generation import GenerationCase
from test_foundation import scratch
from test_master_data import equipment, organization, site
from test_web_site_equipment_d2a import equipment_payload


class WebMasterArchiveBridgeD3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = scratch()
        self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data
        self.client = self.master.create_client(organization("Ateliers du Rhône"))
        self.site = self.master.create_site(self.client.id, site("Atelier principal"))
        self.other_site = self.master.create_site(self.client.id, site("Entrepôt nord"))
        self.equipment = self.master.create_equipment(
            self.site.id, equipment("Pompe à chaleur", "Atelier")
        )
        self.candidate_equipment = self.master.create_equipment(
            self.site.id, equipment("Cassette", "Bureau")
        )
        self.other_equipment = self.master.create_equipment(
            self.other_site.id, equipment("Ventilation", "Réserve")
        )
        self.contract = self.context.contracts.create_draft()
        self.context.contracts.select_client(self.contract.id, self.client.id)
        self.context.contracts.select_site(self.contract.id, self.site.id)
        self.context.contracts.select_equipment(self.contract.id, self.equipment.id)
        self.bridge_actions: list[str] = []
        self.bridge = UiBridge(self.context, self.bridge_actions.append)
        self.bridge.page_name = "CLIENTS"
        self.bridge.selected_client_id = self.client.id

    def tearDown(self) -> None:
        self._scratch.__exit__(None, None, None)

    def child_flags(self) -> tuple[tuple[str, bool], ...]:
        values = []
        for current_site in self.master.list_sites(self.client.id):
            values.append((current_site.id, current_site.archived))
            values.extend(
                (item.id, item.archived)
                for item in self.master.list_equipment(current_site.id)
            )
        return tuple(values)

    def test_archive_restore_exact_client_moves_filter_without_child_cascade(self) -> None:
        self.master.archive_site(self.other_site.id)
        self.master.archive_equipment(self.other_equipment.id)
        children_before = self.child_flags()
        contract_before = self.context.contracts.get(self.contract.id)

        archived = self.bridge.archiveClient(self.client.id)

        self.assertEqual(archived, {"ok": True, "id": self.client.id, "archived": True})
        self.assertTrue(self.master.get_client(self.client.id).archived)
        self.assertEqual(self.child_flags(), children_before)
        self.assertNotIn(
            self.client.id, {row.client.id for row in self.master.list_clients(archived=False)}
        )
        self.assertIn(
            self.client.id, {row.client.id for row in self.master.list_clients(archived=True)}
        )
        archived_snapshot = self.bridge.clients_snapshot()
        self.assertTrue(archived_snapshot["archived"])
        self.assertTrue(archived_snapshot["selected"]["archived"])
        self.assertEqual(archived_snapshot["selected"]["linked"][0]["id"], self.contract.id)
        self.assertEqual(self.context.contracts.get(self.contract.id), contract_before)

        restored = self.bridge.restoreClient(self.client.id)

        self.assertEqual(restored, {"ok": True, "id": self.client.id, "archived": False})
        self.assertFalse(self.master.get_client(self.client.id).archived)
        self.assertEqual(self.child_flags(), children_before)
        self.assertFalse(self.bridge.client_archived)
        self.assertEqual(self.bridge.selected_client_id, self.client.id)

    def test_archive_restore_exact_site_preserves_equipment_and_blocks_new_use(self) -> None:
        equipment_flags = tuple(
            (item.id, item.archived) for item in self.master.list_equipment(self.site.id)
        )
        contract_before = self.context.contracts.get(self.contract.id)

        archived = self.bridge.archiveSite(self.site.id)

        self.assertTrue(archived["ok"])
        self.assertTrue(self.master.get_site(self.site.id).archived)
        self.assertEqual(
            tuple((item.id, item.archived) for item in self.master.list_equipment(self.site.id)),
            equipment_flags,
        )
        self.assertNotIn(
            self.site.id,
            {item.id for item in self.context.contracts.selectable_sites(self.contract.id)},
        )
        self.assertEqual(self.context.contracts.selectable_equipment(self.contract.id), [])
        with self.assertRaises(ContractValidationError):
            self.context.contracts.select_equipment(self.contract.id, self.candidate_equipment.id)
        rejected_contract = self.bridge.createContractForSite(self.site.id)
        rejected_equipment = self.bridge.createEquipment(self.site.id, equipment_payload())
        self.assertFalse(rejected_contract["ok"])
        self.assertFalse(rejected_equipment["ok"])
        self.assertEqual(self.bridge_actions, [])
        self.assertEqual(self.context.contracts.get(self.contract.id), contract_before)

        self.bridge.setShowArchivedMasterData(True)
        archived_projection = self.bridge.clients_snapshot()["selected"]
        projected = next(item for item in archived_projection["sites"] if item["id"] == self.site.id)
        self.assertTrue(projected["archived"])
        self.assertEqual(
            {item["id"] for item in projected["equipment"]},
            {self.equipment.id, self.candidate_equipment.id},
        )

        restored = self.bridge.restoreSite(self.site.id)
        self.assertTrue(restored["ok"])
        self.assertFalse(self.master.get_site(self.site.id).archived)
        self.assertEqual(
            tuple((item.id, item.archived) for item in self.master.list_equipment(self.site.id)),
            equipment_flags,
        )

    def test_archive_restore_exact_equipment_preserves_contract_items_and_projection(self) -> None:
        contract_before = self.context.contracts.get(self.contract.id)
        archived = self.bridge.archiveEquipment(self.equipment.id)
        self.assertTrue(archived["ok"])
        self.assertTrue(self.master.get_equipment(self.equipment.id).archived)
        self.assertNotIn(
            self.equipment.id,
            {item.id for item in self.context.contracts.selectable_equipment(self.contract.id)},
        )
        self.assertEqual(
            self.context.contracts.get(self.contract.id).equipment_items,
            contract_before.equipment_items,
        )
        normal = self.bridge.clients_snapshot()["selected"]["sites"][0]["equipment"]
        self.assertNotIn(self.equipment.id, {item["id"] for item in normal})
        self.bridge.setShowArchivedMasterData(True)
        visible = self.bridge.clients_snapshot()["selected"]["sites"][0]["equipment"]
        projected = next(item for item in visible if item["id"] == self.equipment.id)
        self.assertTrue(projected["archived"])
        restored = self.bridge.restoreEquipment(self.equipment.id)
        self.assertTrue(restored["ok"])
        self.assertFalse(self.master.get_equipment(self.equipment.id).archived)
        self.assertEqual(self.master.get_equipment(self.equipment.id).site_id, self.site.id)

    def test_archived_parent_context_is_authoritatively_unavailable(self) -> None:
        self.master.archive_client(self.client.id)
        self.assertEqual(self.context.contracts.selectable_sites(self.contract.id), [])
        self.assertEqual(self.context.contracts.selectable_equipment(self.contract.id), [])
        with self.assertRaises(ContractValidationError):
            self.context.contracts.select_site(self.contract.id, self.other_site.id)
        with self.assertRaises(ContractValidationError):
            self.context.contracts.select_equipment(self.contract.id, self.candidate_equipment.id)
        self.master.archive_equipment(self.equipment.id)
        self.master.restore_equipment(self.equipment.id)
        self.assertFalse(self.master.get_equipment(self.equipment.id).archived)
        self.assertEqual(self.context.contracts.selectable_equipment(self.contract.id), [])
        rejected_site = self.bridge.createSite(self.client.id, {
            "label": "Refusé", "address_line1": "1 rue Test", "address_line2": "",
            "postal_code": "69000", "city": "Lyon", "country": "France",
            "contact_name": "", "contact_phone": "", "internal_notes": "",
        })
        self.assertFalse(rejected_site["ok"])

    def test_invalid_ids_and_persistence_failure_are_safe(self) -> None:
        before = self.master.get_client(self.client.id)
        for operation in (
            self.bridge.archiveClient, self.bridge.restoreClient,
            self.bridge.archiveSite, self.bridge.restoreSite,
            self.bridge.archiveEquipment, self.bridge.restoreEquipment,
        ):
            result = operation("missing-id")
            self.assertFalse(result["ok"])
        with patch.object(
            self.master.repository, "set_client_archived", side_effect=sqlite3.OperationalError
        ):
            failed = self.bridge.archiveClient(self.client.id)
        self.assertFalse(failed["ok"])
        self.assertEqual(self.master.get_client(self.client.id), before)
        with self.assertRaises(TypeError):
            self.bridge.archiveClient(self.client.id, {"delete": True})


class WebMasterArchiveHistoricalD3Tests(GenerationCase):
    def test_all_archive_restore_intents_preserve_snapshots_items_and_revision(self) -> None:
        generated = self.service().generate(self.contract.id)
        contract_before = self.contracts.get(self.contract.id)
        documents_before = self.documents.list_for_contract(self.contract.id)
        bridge = UiBridge(self.context, lambda action: None)
        bridge.page_name = "CLIENTS"
        bridge.selected_client_id = contract_before.client_source_id
        equipment_id = contract_before.equipment_items[0].source_equipment_id

        self.assertTrue(bridge.archiveClient(contract_before.client_source_id)["ok"])
        self.assertTrue(bridge.archiveSite(contract_before.site_source_id)["ok"])
        self.assertTrue(bridge.archiveEquipment(equipment_id)["ok"])
        self.assertTrue(bridge.restoreEquipment(equipment_id)["ok"])
        self.assertTrue(bridge.restoreSite(contract_before.site_source_id)["ok"])
        self.assertTrue(bridge.restoreClient(contract_before.client_source_id)["ok"])

        contract_after = self.contracts.get(self.contract.id)
        self.assertEqual(contract_after.client_snapshot, contract_before.client_snapshot)
        self.assertEqual(contract_after.site_snapshot, contract_before.site_snapshot)
        self.assertEqual(contract_after.equipment_items, contract_before.equipment_items)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), documents_before)
        self.assertTrue(generated.docx_path.is_file())


class WebMasterArchiveFrontendD3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).parents[1]
        cls.javascript = (root / "src" / "icp_renov_contracts" / "ui_web" / "app.js").read_text(encoding="utf-8")
        cls.bridge = (root / "src" / "icp_renov_contracts" / "ui" / "web_host.py").read_text(encoding="utf-8")

    def test_six_explicit_bridge_intents_and_no_delete_intent_exist(self) -> None:
        for intent in (
            "archiveClient", "restoreClient", "archiveSite", "restoreSite",
            "archiveEquipment", "restoreEquipment",
        ):
            self.assertEqual(self.bridge.count(f"def {intent}("), 1)
        combined = self.javascript + self.bridge
        for forbidden in ("deleteClient", "deleteSite", "deleteEquipment", "Supprimer"):
            self.assertNotIn(forbidden, combined)

    def test_archive_projection_and_controls_are_bounded(self) -> None:
        menu = self.javascript.split("function openSiteMenu(site, trigger) {", 1)[1].split(
            "function findEquipmentContext", 1
        )[0]
        for label in (
            "Modifier le site", "Ajouter un équipement", "Archiver le site", "Restaurer le site",
        ):
            self.assertIn(label, menu)
        self.assertNotIn("Dupliquer", menu)
        self.assertNotIn("Déplacer", menu)
        self.assertIn("Afficher les archivés", self.javascript)
        self.assertIn("Archiver l’équipement", self.javascript)
        self.assertIn("Restaurer cette fiche", self.javascript)
        self.assertIn("Les contrats existants restent inchangés et accessibles.", self.javascript)
        self.assertIn("onclick=\"closeArchiveDialog()\">Annuler", self.javascript)


if __name__ == "__main__":
    unittest.main()
