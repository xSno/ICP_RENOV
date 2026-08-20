from __future__ import annotations

import sqlite3
from pathlib import Path
import unittest
from unittest import mock

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.domain import ContractStatus
from icp_renov_contracts.ui.web_host import UiBridge

from test_foundation import scratch
from test_master_data import equipment, organization, person, site


def client_payload(name="Client créé Web"):
    return {
        "party_type": "ORGANIZATION", "first_name": "", "last_name": "",
        "organization_name": name, "legal_form": "SARL", "siret": "12345678900011",
        "address_line1": "1 rue du Web", "address_line2": "", "postal_code": "69001",
        "city": "Lyon", "country": "France", "billing_address": "", "phone": "0400000000",
        "email": "web@example.test", "internal_reference": "WEB", "internal_notes": "secret client",
    }


def site_payload(label="Site créé Web"):
    return {
        "label": label, "address_line1": "2 rue du Web", "address_line2": "",
        "postal_code": "69002", "city": "Lyon", "country": "France",
        "contact_name": "Contact", "contact_phone": "0411111111", "internal_notes": "secret site",
    }


def equipment_payload(location="Local Web"):
    return {
        "equipment_type": "Cassette", "brand": "Daikin", "model": "Web",
        "serial_number": "WEB-1", "power_kw": "3.5", "location": location,
        "installation_date": "2026-08-20", "internal_reference": "EQ-WEB",
        "internal_notes": "SECRET MASTER",
    }


class WebContractWorkspaceW3ATests(unittest.TestCase):
    def setUp(self):
        self._scratch = scratch(); self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data; self.contracts = self.context.contracts
        self.client_a = self.master.create_client(organization("Ateliers du Rhône"))
        self.client_b = self.master.create_client(person("Élise", "Martin"))
        self.site_a = self.master.create_site(self.client_a.id, site("Atelier principal"))
        self.site_b = self.master.create_site(self.client_a.id, site("Entrepôt nord"))
        self.foreign_site = self.master.create_site(self.client_b.id, site("Domicile"))
        self.eq_a = self.master.create_equipment(self.site_a.id, equipment("Pompe", "Local A", internal_notes="SECRET A"))
        self.eq_b = self.master.create_equipment(self.site_a.id, equipment("Ventilation", "Local B", internal_notes="SECRET B"))
        self.eq_other = self.master.create_equipment(self.site_b.id, equipment("Groupe", "Toit"))
        self.actions = []; self.bridge = UiBridge(self.context, self.actions.append)
        self.states = []; self.bridge.stateChanged.connect(self.states.append)

    def tearDown(self): self._scratch.__exit__(None, None, None)

    def populated(self):
        contract = self.contracts.create_draft()
        self.contracts.select_client(contract.id, self.client_a.id)
        self.contracts.select_site(contract.id, self.site_a.id)
        self.contracts.select_equipment(contract.id, self.eq_a.id)
        return self.contracts.get(contract.id)

    def test_new_and_existing_exact_id_routes_share_one_web_workspace(self):
        existing = self.populated()
        opened = self.bridge.openContract(existing.id)
        self.assertEqual(opened, {"ok": True, "id": existing.id})
        self.assertEqual((self.bridge.page_name, self.bridge.contract_id), ("CONTRACT_WORKSPACE", existing.id))
        self.assertEqual(self.states[-1]["contract"]["id"], existing.id)
        self.bridge.returnToContracts(); created = self.bridge.createContract()
        self.assertTrue(created["ok"]); self.assertIs(self.contracts.get(created["id"]).status, ContractStatus.DRAFT)
        self.assertEqual(self.states[-1]["contract"]["number"], "Brouillon sans numéro")

    def test_create_for_site_prefills_exact_context_once_without_regime_or_equipment(self):
        before = {item.id for item in self.contracts.list_drafts()}
        result = self.bridge.createContractForSite(self.site_a.id)
        after = {item.id for item in self.contracts.list_drafts()}
        self.assertEqual(after - before, {result["id"]})
        contract = self.contracts.get(result["id"])
        self.assertEqual((contract.client_source_id, contract.site_source_id), (self.client_a.id, self.site_a.id))
        self.assertIsNone(contract.regime); self.assertEqual(contract.equipment_items, ())
        self.assertEqual(self.bridge.page_name, "CONTRACT_WORKSPACE")

    def test_client_change_cascade_and_master_separation(self):
        contract = self.populated(); item = contract.equipment_items[0]
        self.contracts.update_observation(contract.id, item.id, "Observation ancienne")
        master_before = self.master.get_equipment(self.eq_a.id)
        result = self.bridge.selectContractClient(contract.id, self.client_b.id)
        changed = self.contracts.get(contract.id)
        self.assertTrue(result["ok"]); self.assertEqual(changed.client_source_id, self.client_b.id)
        self.assertIsNone(changed.site_source_id); self.assertEqual(changed.equipment_items, ())
        self.assertEqual((changed.signatory_name, changed.signatory_role), ("Élise Martin", "Client"))
        self.assertEqual(self.master.get_equipment(self.eq_a.id), master_before)

    def test_site_change_clears_items_and_limits_exact_context(self):
        contract = self.populated(); item = contract.equipment_items[0]
        self.contracts.update_observation(contract.id, item.id, "À effacer")
        self.bridge.openContract(contract.id)
        result = self.bridge.selectContractSite(contract.id, self.site_b.id)
        changed = self.contracts.get(contract.id)
        self.assertTrue(result["ok"]); self.assertEqual(changed.site_source_id, self.site_b.id)
        self.assertEqual(changed.equipment_items, ())
        state = self.states[-1]
        self.assertEqual({row["id"] for row in state["sites"]}, {self.site_a.id, self.site_b.id})
        self.assertEqual({row["id"] for row in state["equipment"]}, {self.eq_other.id})
        self.assertNotIn(self.foreign_site.id, {row["id"] for row in state["sites"]})

    def test_equipment_checkbox_order_observation_and_internal_note_boundary(self):
        contract = self.populated()
        self.assertTrue(self.bridge.setContractEquipment(contract.id, self.eq_b.id, True)["ok"])
        items = self.contracts.get(contract.id).equipment_items
        self.assertEqual([x.source_equipment_id for x in items], [self.eq_a.id, self.eq_b.id])
        self.assertTrue(self.bridge.moveContractEquipment(contract.id, items[1].id, -1)["ok"])
        moved = self.contracts.get(contract.id).equipment_items
        self.assertEqual([x.source_equipment_id for x in moved], [self.eq_b.id, self.eq_a.id])
        self.assertTrue(self.bridge.updateContractEquipmentObservation(contract.id, moved[0].id, "Observation contrat")["ok"])
        observed = self.contracts.get(contract.id).equipment_items[0]
        self.assertEqual(observed.observation, "Observation contrat")
        self.assertEqual(self.master.get_equipment(self.eq_b.id).internal_notes, "SECRET B")
        self.assertNotIn("SECRET", observed.snapshot.to_json())
        self.assertTrue(self.bridge.setContractEquipment(contract.id, self.eq_b.id, False)["ok"])
        self.assertEqual([x.position for x in self.contracts.get(contract.id).equipment_items], [0])

    def test_inline_master_creation_selects_exact_contract_context_and_appends_last(self):
        contract = self.contracts.create_draft()
        client_result = self.bridge.createContractClient(contract.id, client_payload())
        self.assertTrue(client_result["ok"]); client = self.master.get_client(client_result["client_id"])
        self.assertIsNone(self.contracts.get(contract.id).regime)
        site_result = self.bridge.createContractSite(contract.id, site_payload())
        self.assertTrue(site_result["ok"]); created_site = self.master.get_site(site_result["site_id"])
        self.assertEqual(created_site.client_id, client.id)
        first = self.bridge.createContractEquipment(contract.id, equipment_payload("Premier"))
        second = self.bridge.createContractEquipment(contract.id, equipment_payload("Dernier"))
        self.assertTrue(first["ok"] and second["ok"])
        stored = self.contracts.get(contract.id)
        self.assertEqual([x.source_equipment_id for x in stored.equipment_items], [first["equipment_id"], second["equipment_id"]])
        self.assertEqual(stored.equipment_items[-1].position, 1)
        self.assertEqual(self.master.get_equipment(second["equipment_id"]).site_id, created_site.id)

    def test_archived_authority_and_historical_snapshot_projection(self):
        contract = self.populated(); self.master.archive_client(self.client_b.id)
        self.master.archive_site(self.site_b.id); self.master.archive_equipment(self.eq_b.id)
        self.bridge.openContract(contract.id); state = self.states[-1]
        self.assertNotIn(self.client_b.id, {row["id"] for row in state["clients"]})
        self.assertNotIn(self.site_b.id, {row["id"] for row in state["sites"]})
        self.assertNotIn(self.eq_b.id, {row["id"] for row in state["equipment"]})
        self.master.archive_client(self.client_a.id)
        historical = self.bridge.contract_workspace_snapshot()
        self.assertEqual(historical["contract"]["client"], "Ateliers du Rhône")
        self.assertEqual(historical["equipment"][0]["id"], self.eq_a.id)

    def test_zero_equipment_draft_persists_but_review_is_not_ready(self):
        contract = self.contracts.create_draft(); self.contracts.select_client(contract.id, self.client_a.id)
        contract = self.contracts.select_site(contract.id, self.site_a.id)
        self.bridge.openContract(contract.id); state = self.states[-1]
        self.assertEqual(contract.equipment_items, ()); self.assertEqual(state["summary"]["completion"], "Étape 1 à compléter")
        issues = [issue.message for block in self.context.review.review(contract.id).blocks for issue in block.issues]
        self.assertIn("Aucun équipement sélectionné.", issues)

    def test_master_edits_do_not_rewrite_snapshots_and_failure_has_no_false_success(self):
        contract = self.populated(); before = self.contracts.get(contract.id)
        self.master.update_client(self.client_a.id, organization("Nom maître modifié"))
        self.master.update_site(self.site_a.id, site("Site maître modifié"))
        self.master.update_equipment(self.eq_a.id, equipment("Maître modifié", "Autre"))
        unchanged = self.contracts.get(contract.id)
        self.assertEqual((unchanged.client_snapshot, unchanged.site_snapshot, unchanged.equipment_items),
                         (before.client_snapshot, before.site_snapshot, before.equipment_items))
        emissions = len(self.states)
        with mock.patch.object(self.contracts.repository, "update_signatory", side_effect=sqlite3.OperationalError("disk")):
            result = self.bridge.updateContractSignatory(contract.id, "Non persisté", "Erreur")
        self.assertFalse(result["ok"]); self.assertEqual(len(self.states), emissions)
        self.assertNotEqual(self.contracts.get(contract.id).signatory_name, "Non persisté")

    def test_frontend_is_four_step_bounded_and_has_no_generic_contract_mutator(self):
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts"
        js = (root / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        bridge = (root / "ui" / "web_host.py").read_text(encoding="utf-8")
        for label in ("Client, site & équipements", "Conditions du contrat", "Revue", "Documents & suivi"):
            self.assertIn(label, js)
        for removed_copy in (
            "Migration Web à venir",
            "Sélection par identifiant exact parmi les fiches actives.",
            "Contexte du brouillon mis à jour",
        ):
            self.assertNotIn(removed_copy, js)
        for user_copy in (
            "Sélectionnez une fiche client active.",
            "Sélectionnez un site actif pour ce client.",
            "Effet du changement",
        ):
            self.assertIn(user_copy, js)
        self.assertIn("Les dernières modifications ne sont pas encore enregistrées", js)
        self.assertIn("internal_notes", bridge)
        for intent in ("selectContractClient", "updateContractSignatory", "selectContractSite", "setContractEquipment",
                       "moveContractEquipment", "updateContractEquipmentObservation"):
            self.assertIn(f"def {intent}", bridge)
        for forbidden in ("patchContract", "updateEntity", "dispatchContract", "exec(", "eval("):
            self.assertNotIn(forbidden, bridge)


if __name__ == "__main__": unittest.main()
