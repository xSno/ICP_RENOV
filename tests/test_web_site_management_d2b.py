from __future__ import annotations

from pathlib import Path
import unittest

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.ui.web_host import UiBridge

from test_document_generation import GenerationCase
from test_foundation import scratch
from test_master_data import equipment, organization, site
from test_web_site_equipment_d2a import equipment_payload, site_payload


class WebSiteManagementBridgeD2BTests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = scratch()
        self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data
        self.client = self.master.create_client(organization("Ateliers du Rhône"))
        self.other_client = self.master.create_client(organization("Bâtiments Horizon"))
        self.site = self.master.create_site(self.client.id, site("Atelier principal"))
        self.equipment = self.master.create_equipment(
            self.site.id, equipment("Cassette", "Atelier")
        )
        self.bridge = UiBridge(self.context, lambda action: None)
        self.bridge.page_name = "CLIENTS"
        self.bridge.selected_client_id = self.client.id

    def tearDown(self) -> None:
        self._scratch.__exit__(None, None, None)

    def test_snapshot_exposes_closed_site_editor_and_update_uses_exact_id(self) -> None:
        presented = self.bridge.clients_snapshot()["selected"]["sites"][0]
        self.assertEqual(presented["id"], self.site.id)
        self.assertEqual(set(presented["editor"]), {
            "label", "address_line1", "address_line2", "postal_code", "city", "country",
            "contact_name", "contact_phone", "internal_notes",
        })
        result = self.bridge.updateSite(
            self.site.id,
            site_payload(label="Atelier principal", address_line2="Accès nord"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], self.site.id)
        self.assertEqual(result["client_id"], self.client.id)
        self.assertEqual(self.bridge.selected_client_id, self.client.id)
        updated = self.master.get_site(self.site.id)
        self.assertEqual(updated.address_line2, "Accès nord")
        self.assertEqual(updated.client_id, self.client.id)
        self.assertEqual(self.master.get_equipment(self.equipment.id).site_id, self.site.id)

    def test_update_cannot_change_client_and_rejects_validation_without_mutation(self) -> None:
        original = self.master.get_site(self.site.id)
        unsupported = self.bridge.updateSite(
            self.site.id, {**site_payload(), "client_id": self.other_client.id}
        )
        self.assertFalse(unsupported["ok"])
        self.assertIn("payload", unsupported["field_errors"])
        invalid = self.bridge.updateSite(self.site.id, site_payload(label="", city=""))
        self.assertFalse(invalid["ok"])
        self.assertEqual(set(invalid["field_errors"]), {"label", "city"})
        malformed = self.bridge.updateSite(self.site.id, "site")
        self.assertFalse(malformed["ok"])
        self.assertEqual(self.master.get_site(self.site.id), original)

    def test_cancel_equivalent_and_failed_add_equipment_are_mutation_free(self) -> None:
        site_before = self.master.get_site(self.site.id)
        equipment_before = self.master.list_equipment(self.site.id)
        self.bridge.refresh()
        rejected = self.bridge.createEquipment(
            self.site.id, equipment_payload(equipment_type="", location="")
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(self.master.get_site(self.site.id), site_before)
        self.assertEqual(self.master.list_equipment(self.site.id), equipment_before)


class WebSiteManagementContractImmutabilityD2BTests(GenerationCase):
    def test_site_edit_and_populated_site_equipment_add_preserve_contract_authority(self) -> None:
        contract_before = self.contracts.get(self.contract.id)
        documents_before = self.documents.list_for_contract(self.contract.id)
        bridge = UiBridge(self.context, lambda action: None)
        bridge.page_name = "CLIENTS"
        bridge.selected_client_id = contract_before.client_source_id
        source_site_id = contract_before.site_source_id
        original_equipment_ids = {
            item.source_equipment_id for item in contract_before.equipment_items
        }

        updated = bridge.updateSite(
            source_site_id,
            site_payload(label="Atelier principal", address_line2="Entrée nord"),
        )
        created = bridge.createEquipment(
            source_site_id,
            equipment_payload(equipment_type="Pompe à chaleur", location="Toiture"),
        )

        self.assertTrue(updated["ok"] and created["ok"])
        self.assertEqual(updated["id"], source_site_id)
        self.assertEqual(created["site_id"], source_site_id)
        contract_after = self.contracts.get(self.contract.id)
        self.assertEqual(contract_after.client_snapshot, contract_before.client_snapshot)
        self.assertEqual(contract_after.site_snapshot, contract_before.site_snapshot)
        self.assertEqual(contract_after.equipment_items, contract_before.equipment_items)
        self.assertEqual(
            {item.source_equipment_id for item in contract_after.equipment_items},
            original_equipment_ids,
        )
        self.assertNotIn(created["id"], original_equipment_ids)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), documents_before)


class WebSiteManagementFrontendD2BTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "app.js"
        ).read_text(encoding="utf-8")

    def test_site_menu_preserves_the_two_d2b_actions_before_d3_archive_extension(self) -> None:
        block = self.source.split("function openSiteMenu(site, trigger) {", 1)[1].split(
            "function findEquipmentContext", 1
        )[0]
        self.assertLess(block.index("'Modifier le site'"), block.index("'Ajouter un équipement'"))
        self.assertLess(block.index("'Ajouter un équipement'"), block.index("'Archiver le site'"))
        self.assertNotIn("Supprimer", block)
        self.assertIn("openSiteDrawer(trigger, 'edit', site.id)", block)
        self.assertIn("openEquipmentDrawer('create', site.id, trigger)", block)
        self.assertIn("!clientsState.selected.archived && site.active_equipment_count > 0", block)

    def test_zero_active_site_has_disabled_contract_action_and_one_standalone_add(self) -> None:
        block = self.source.split("function renderClients(state) {", 1)[1].split("const linked =", 1)[0]
        self.assertIn("const hasActiveEquipment = site.active_equipment_count > 0", block)
        action = block.split("const contractAction =", 1)[1].split("const zeroActiveEquipment =", 1)[0]
        enabled, disabled = action.split("\n      :", 1)
        self.assertIn("hasActiveEquipment", enabled)
        self.assertIn("bridge.createContractForSite('${site.id}')", enabled)
        self.assertIn('disabled aria-disabled="true"', disabled)
        self.assertNotIn("onclick", disabled)
        self.assertNotIn("bridge.createContractForSite", disabled)
        zero = block.split("const zeroActiveEquipment =", 1)[1].split("const emptyEquipment =", 1)[0]
        self.assertIn("mayCreate && !hasActiveEquipment", zero)
        self.assertIn("Ajoutez au moins un équipement actif pour créer un contrat depuis ce site.", zero)
        self.assertIn('class="empty-list zero-active-equipment"', zero)
        self.assertIn('class="zero-active-equipment-message"', zero)
        self.assertIn('depuis ce site.</div><div class="zero-active-equipment-action">', zero)
        self.assertEqual(zero.count('class="button button-secondary zero-active-add-equipment"'), 1)
        self.assertIn("${icon('plus')}Ajouter un équipement", zero)
        self.assertNotIn("add-inline", zero)
        self.assertNotIn("zero-active-add-equipment", block.split("const emptyEquipment =", 1)[1])
        self.assertIn("${equipmentRows || (zeroActiveEquipment ? '' : emptyEquipment)}${zeroActiveEquipment}", block)
        binding = self.source.split("const addEquipment =", 1)[1].split("card.querySelectorAll('.equipment-row')", 1)[0]
        self.assertIn("card.querySelector('.zero-active-add-equipment')", binding)
        self.assertIn("openEquipmentDrawer('create', site.id, event.currentTarget)", binding)
        menu = self.source.split("function openSiteMenu(site, trigger) {", 1)[1].split("function findEquipmentContext", 1)[0]
        self.assertIn("if (!clientsState.selected.archived && site.active_equipment_count > 0)", menu)

    def test_site_menu_and_drawer_keep_exact_ownership_and_existing_create_path(self) -> None:
        self.assertEqual(self.source.count("function openEquipmentDrawer("), 1)
        self.assertIn("siteActions.setAttribute('aria-label', `Actions du site ${site.label}`)", self.source)
        self.assertIn("siteId: site?.id || null, clientId: client.id", self.source)
        self.assertIn("bridge.updateSite(entityDrawer.siteId, entityDrawer.payload, callback)", self.source)
        self.assertIn("bridge.createEquipment(entityDrawer.siteId, entityDrawer.payload, callback)", self.source)
        self.assertIn("if (siteMenu) closeSiteMenu()", self.source)
        self.assertIn("!siteMenu.popover.contains(event.target)", self.source)


if __name__ == "__main__":
    unittest.main()
