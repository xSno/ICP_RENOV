from __future__ import annotations

from pathlib import Path
import unittest

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.ui.web_host import UiBridge

from test_document_generation import GenerationCase
from test_foundation import scratch
from test_master_data import equipment, organization, site


def site_payload(**overrides) -> dict[str, str]:
    values = {
        "label": "Agence D2A", "address_line1": "18 quai Saint-Vincent",
        "address_line2": "Bâtiment B", "postal_code": "69001", "city": "Lyon",
        "country": "France", "contact_name": "Alex Martin", "contact_phone": "04 72 00 00 10",
        "internal_notes": "Accès par la cour",
    }
    values.update(overrides)
    return values


def equipment_payload(**overrides) -> dict[str, str]:
    values = {
        "equipment_type": "Unité murale", "brand": "Daikin", "model": "Perfera",
        "serial_number": "D2A-0001", "power_kw": "3.5", "location": "Accueil",
        "installation_date": "2026-08-20", "internal_reference": "EQ-D2A",
        "internal_notes": "Interne uniquement",
    }
    values.update(overrides)
    return values


class WebSiteEquipmentBridgeD2ATests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = scratch()
        self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data
        self.client = self.master.create_client(organization("Ateliers du Rhône"))
        self.other_client = self.master.create_client(organization("Bâtiments Horizon"))
        self.empty_site = self.master.create_site(self.client.id, site("Site vide"))
        self.populated_site = self.master.create_site(self.client.id, site("Atelier principal"))
        self.original_equipment = self.master.create_equipment(
            self.populated_site.id, equipment("Cassette", "Atelier", internal_notes="Secret maître")
        )
        self.bridge = UiBridge(self.context, lambda action: None)
        self.bridge.page_name = "CLIENTS"
        self.bridge.selected_client_id = self.client.id
        self.states: list[dict] = []
        self.bridge.stateChanged.connect(self.states.append)

    def tearDown(self) -> None:
        self._scratch.__exit__(None, None, None)

    def test_create_site_uses_exact_client_and_returns_authoritative_id(self) -> None:
        result = self.bridge.createSite(self.other_client.id, site_payload(label="Site Horizon"))
        self.assertTrue(result["ok"])
        created = self.master.get_site(result["id"])
        self.assertEqual(created.client_id, self.other_client.id)
        self.assertEqual(result["client_id"], self.other_client.id)
        self.assertEqual(self.bridge.selected_client_id, self.other_client.id)
        self.assertIn(created.id, {item.id for item in self.master.list_sites(self.other_client.id)})
        self.assertNotIn(created.id, {item.id for item in self.master.list_sites(self.client.id)})

    def test_site_validation_and_closed_dto_create_no_phantom(self) -> None:
        before = len(self.master.list_sites(self.client.id))
        invalid = self.bridge.createSite(self.client.id, site_payload(label="", city=""))
        self.assertFalse(invalid["ok"])
        self.assertEqual(set(invalid["field_errors"]), {"label", "city"})
        unsupported = self.bridge.createSite(
            self.client.id, {**site_payload(), "client_id": self.other_client.id}
        )
        self.assertFalse(unsupported["ok"])
        self.assertIn("payload", unsupported["field_errors"])
        self.assertEqual(len(self.master.list_sites(self.client.id)), before)

    def test_copied_client_address_is_an_independent_site_value(self) -> None:
        current = self.master.get_client(self.client.id)
        copied = site_payload(
            address_line1=current.address_line1, address_line2=current.address_line2,
            postal_code=current.postal_code, city=current.city, country=current.country,
        )
        result = self.bridge.createSite(self.client.id, copied)
        created = self.master.get_site(result["id"])
        before = created.rendered_address
        self.master.update_client(
            self.client.id,
            organization("Ateliers du Rhône", address_line1="99 rue Modifiée", city="Grenoble"),
        )
        self.assertEqual(self.master.get_site(created.id).rendered_address, before)
        self.assertNotEqual(self.master.get_client(self.client.id).rendered_address, before)

    def test_create_equipment_uses_exact_site_and_validates_required_fields(self) -> None:
        result = self.bridge.createEquipment(self.empty_site.id, equipment_payload())
        self.assertTrue(result["ok"])
        created = self.master.get_equipment(result["id"])
        self.assertEqual(created.site_id, self.empty_site.id)
        self.assertEqual(result["site_id"], self.empty_site.id)
        invalid_type = self.bridge.createEquipment(
            self.empty_site.id, equipment_payload(equipment_type="")
        )
        invalid_location = self.bridge.createEquipment(
            self.empty_site.id, equipment_payload(location="")
        )
        self.assertIn("equipment_type", invalid_type["field_errors"])
        self.assertIn("location", invalid_location["field_errors"])

    def test_update_exact_equipment_cannot_move_site(self) -> None:
        result = self.bridge.updateEquipment(
            self.original_equipment.id,
            equipment_payload(equipment_type="Cassette", location="Atelier nord", brand="Mitsubishi"),
        )
        self.assertEqual(result["id"], self.original_equipment.id)
        updated = self.master.get_equipment(self.original_equipment.id)
        self.assertEqual(updated.location, "Atelier nord")
        self.assertEqual(updated.site_id, self.populated_site.id)
        rejected = self.bridge.updateEquipment(
            self.original_equipment.id,
            {**equipment_payload(), "site_id": self.empty_site.id},
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("payload", rejected["field_errors"])
        self.assertEqual(self.master.get_equipment(self.original_equipment.id).site_id, self.populated_site.id)

    def test_internal_notes_never_become_contract_observation_or_new_selection(self) -> None:
        draft = self.context.contracts.create_draft()
        self.context.contracts.select_client(draft.id, self.client.id)
        self.context.contracts.select_site(draft.id, self.populated_site.id)
        selected = self.context.contracts.select_equipment(draft.id, self.original_equipment.id)
        item = selected.equipment_items[0]
        self.context.contracts.update_observation(draft.id, item.id, "Observation contrat")
        created = self.bridge.createEquipment(
            self.populated_site.id, equipment_payload(internal_notes="Nouveau secret maître")
        )
        before = self.context.contracts.get(draft.id)
        result = self.bridge.updateEquipment(
            self.original_equipment.id,
            equipment_payload(equipment_type="Cassette", internal_notes="Secret maître modifié"),
        )
        self.assertTrue(result["ok"])
        after = self.context.contracts.get(draft.id)
        self.assertEqual(after.equipment_items, before.equipment_items)
        self.assertEqual(after.equipment_items[0].observation, "Observation contrat")
        self.assertNotIn(created["id"], {value.source_equipment_id for value in after.equipment_items})

    def test_malformed_payload_and_cancel_equivalent_are_mutation_free(self) -> None:
        site_count = len(self.master.list_sites(self.client.id))
        equipment_count = len(self.master.list_equipment(self.empty_site.id))
        self.bridge.refresh()
        self.assertEqual(len(self.master.list_sites(self.client.id)), site_count)
        self.assertEqual(len(self.master.list_equipment(self.empty_site.id)), equipment_count)
        for operation in (
            lambda: self.bridge.createSite(self.client.id, "site"),
            lambda: self.bridge.createEquipment(self.empty_site.id, {**equipment_payload(), "power_kw": 3.5}),
            lambda: self.bridge.updateEquipment(self.original_equipment.id, {**equipment_payload(), "client_id": self.client.id}),
        ):
            self.assertFalse(operation()["ok"])
        self.assertEqual(len(self.master.list_sites(self.client.id)), site_count)
        self.assertEqual(len(self.master.list_equipment(self.empty_site.id)), equipment_count)


class WebSiteEquipmentContractImmutabilityD2ATests(GenerationCase):
    def test_site_create_equipment_create_and_edit_preserve_snapshot_revision_items_and_observation(self) -> None:
        source = self.contracts.get(self.contract.id)
        item_id = source.equipment_items[0].id
        self.contracts.update_observation(self.contract.id, item_id, "Observation du contrat")
        generated = self.service().generate(self.contract.id)
        contract_before = self.contracts.get(self.contract.id)
        documents_before = self.documents.list_for_contract(self.contract.id)
        bridge = UiBridge(self.context, lambda action: None)
        bridge.page_name = "CLIENTS"
        bridge.selected_client_id = contract_before.client_source_id
        created_site = bridge.createSite(contract_before.client_source_id, site_payload())
        created_equipment = bridge.createEquipment(created_site["id"], equipment_payload())
        master_equipment_id = contract_before.equipment_items[0].source_equipment_id
        updated = bridge.updateEquipment(
            master_equipment_id,
            equipment_payload(equipment_type="Unité Snapshot", location="Nouvelle zone", internal_notes="Interne"),
        )
        self.assertTrue(created_site["ok"] and created_equipment["ok"] and updated["ok"])
        contract_after = self.contracts.get(self.contract.id)
        self.assertEqual(contract_after.client_snapshot, contract_before.client_snapshot)
        self.assertEqual(contract_after.site_snapshot, contract_before.site_snapshot)
        self.assertEqual(contract_after.equipment_items, contract_before.equipment_items)
        self.assertEqual(contract_after.equipment_items[0].observation, "Observation du contrat")
        self.assertEqual(self.documents.list_for_contract(self.contract.id), documents_before)
        self.assertTrue(generated.docx_path.is_file())
