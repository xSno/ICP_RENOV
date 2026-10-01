from __future__ import annotations

from pathlib import Path
import unittest

from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.ui.web_host import CLIENT_DTO_FIELDS, UiBridge

from test_document_generation import GenerationCase
from test_foundation import scratch
from test_master_data import organization, site


def person_payload(**overrides) -> dict[str, str]:
    values = {
        "party_type": "PERSON", "first_name": "Élise", "last_name": "Martin",
        "organization_name": "", "legal_form": "", "siret": "",
        "address_line1": "8 rue des Fleurs", "address_line2": "", "postal_code": "69003",
        "city": "Lyon", "country": "France", "billing_address": "",
        "phone": "04 70 00 00 01", "email": "elise@example.invalid",
        "internal_reference": "CLI-ELISE", "internal_notes": "Contact le matin",
    }
    values.update(overrides)
    return values


def organization_payload(**overrides) -> dict[str, str]:
    values = {
        "party_type": "ORGANIZATION", "first_name": "", "last_name": "",
        "organization_name": "Bâtiments Horizon", "legal_form": "SAS", "siret": "12345678901234",
        "address_line1": "12 quai Saint-Vincent", "address_line2": "", "postal_code": "69001",
        "city": "Lyon", "country": "France", "billing_address": "Service comptabilité, 69001 Lyon",
        "phone": "04 70 00 00 02", "email": "contact@horizon.example",
        "internal_reference": "CLI-HORIZON", "internal_notes": "Référence maître",
    }
    values.update(overrides)
    return values


class WebClientBridgeD1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = scratch()
        self.temporary = Path(self._scratch.__enter__())
        store = MachineConfigStore(self.temporary / "bootstrap.json")
        store.save(BootstrapConfig(self.temporary / "workspace"))
        self.context = build_application_context(config_store=store)
        self.master = self.context.master_data
        self.original = self.master.create_client(organization(
            "Ateliers du Rhône", proposed_contact_name="Camille Signataire",
            proposed_contact_role="Gérante",
        ))
        self.site = self.master.create_site(self.original.id, site("Atelier principal"))
        self.actions: list[str] = []
        self.bridge = UiBridge(self.context, self.actions.append)
        self.states: list[dict] = []
        self.bridge.stateChanged.connect(self.states.append)
        self.bridge.navigate("CLIENTS")

    def tearDown(self) -> None:
        self._scratch.__exit__(None, None, None)

    def test_create_person_returns_and_selects_authoritative_id(self) -> None:
        result = self.bridge.createClient(person_payload())
        self.assertTrue(result["ok"])
        created = self.master.get_client(result["id"])
        self.assertEqual(created.display_name, "Élise Martin")
        self.assertEqual(self.bridge.selected_client_id, created.id)
        self.assertEqual(self.states[-1]["selected"]["id"], created.id)

    def test_create_organization_persists_supported_fields_only(self) -> None:
        result = self.bridge.createClient(organization_payload())
        self.assertTrue(result["ok"])
        created = self.master.get_client(result["id"])
        self.assertEqual(created.organization_name, "Bâtiments Horizon")
        self.assertEqual(created.billing_address, "Service comptabilité, 69001 Lyon")
        self.assertEqual(set(self.states[-1]["selected"]["editor"]), CLIENT_DTO_FIELDS)
        self.assertFalse(any("regime" in field for field in CLIENT_DTO_FIELDS))

    def test_client_search_is_python_authoritative_and_clearing_restores_the_list(self) -> None:
        self.master.create_client(organization("Bâtiments Horizon"))
        self.bridge.setClientSearch("Ateliers")
        self.assertEqual([item["name"] for item in self.states[-1]["clients"]], ["Ateliers du Rhône"])
        self.bridge.setClientSearch("")
        self.assertEqual(len(self.states[-1]["clients"]), 2)

    def test_validation_failure_and_unsupported_regime_create_no_client(self) -> None:
        before = len(self.master.list_clients())
        invalid = self.bridge.createClient(person_payload(first_name="", email="invalide"))
        self.assertFalse(invalid["ok"])
        self.assertIn("first_name", invalid["field_errors"])
        self.assertEqual(len(self.master.list_clients()), before)
        unsupported = self.bridge.createClient({**person_payload(), "regime": "PROFESSIONAL"})
        self.assertFalse(unsupported["ok"])
        self.assertIn("payload", unsupported["field_errors"])
        self.assertEqual(len(self.master.list_clients()), before)

    def test_malformed_payload_is_rejected_without_mutation(self) -> None:
        before = len(self.master.list_clients())
        for payload in ("client", {**person_payload(), "phone": 1234}):
            result = self.bridge.createClient(payload)
            self.assertFalse(result["ok"])
        self.assertEqual(len(self.master.list_clients()), before)

    def test_update_exact_id_preserves_type_site_and_hidden_contact_fields(self) -> None:
        payload = self.bridge.clients_snapshot()["selected"]["editor"]
        payload = {**payload, "organization_name": "Ateliers du Rhône Services", "phone": "04 72 10 20 30"}
        result = self.bridge.updateClient(self.original.id, payload)
        self.assertEqual(result, {"ok": True, "id": self.original.id})
        updated = self.master.get_client(self.original.id)
        self.assertEqual(updated.organization_name, "Ateliers du Rhône Services")
        self.assertEqual(updated.party_type, "ORGANIZATION")
        self.assertEqual(updated.proposed_contact_name, "Camille Signataire")
        self.assertEqual(updated.proposed_contact_role, "Gérante")
        self.assertEqual(self.master.get_site(self.site.id).client_id, self.original.id)
        self.assertEqual(self.states[-1]["selected"]["id"], self.original.id)

    def test_update_rejects_type_change_and_wrong_id(self) -> None:
        payload = self.bridge.clients_snapshot()["selected"]["editor"]
        changed_type = self.bridge.updateClient(self.original.id, {
            **payload, "party_type": "PERSON", "first_name": "Élise", "last_name": "Martin",
        })
        self.assertFalse(changed_type["ok"])
        self.assertIn("party_type", changed_type["field_errors"])
        missing = self.bridge.updateClient("missing-client", payload)
        self.assertFalse(missing["ok"])
        self.assertEqual(self.master.get_client(self.original.id).party_type, "ORGANIZATION")

    def test_authoritative_snapshot_and_cancel_equivalent_are_read_only(self) -> None:
        before = self.master.get_client(self.original.id)
        states_before = len(self.states)
        self.bridge.refresh()
        self.assertGreater(len(self.states), states_before)
        self.assertEqual(self.master.get_client(self.original.id), before)


class WebClientContractImmutabilityD1Tests(GenerationCase):
    def test_update_does_not_rewrite_contract_snapshot_or_existing_revision(self) -> None:
        source_client_id = self.contracts.get(self.contract.id).client_source_id
        generated = self.service().generate(self.contract.id)
        contract_before = self.contracts.get(self.contract.id)
        snapshot_before = contract_before.client_snapshot.to_json()
        documents_before = self.documents.list_for_contract(self.contract.id)
        site_id = contract_before.site_source_id
        bridge = UiBridge(self.context, lambda action: None)
        bridge.page_name = "CLIENTS"
        bridge.selected_client_id = source_client_id
        payload = bridge.clients_snapshot()["selected"]["editor"]
        result = bridge.updateClient(
            source_client_id, {**payload, "organization_name": "Maître modifié après révision"}
        )
        self.assertTrue(result["ok"])
        contract_after = self.contracts.get(self.contract.id)
        self.assertEqual(contract_after.client_snapshot.to_json(), snapshot_before)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), documents_before)
        self.assertEqual(contract_after.site_source_id, site_id)
        self.assertTrue(generated.docx_path.is_file())
