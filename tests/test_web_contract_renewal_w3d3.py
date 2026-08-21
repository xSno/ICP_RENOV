from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import ContractEventType, ContractStatus, standard_end_date
from icp_renov_contracts.errors import ContractConditionsValidationError, ContractLifecycleError
from icp_renov_contracts.repositories import ContractEventRepository
from icp_renov_contracts.services import ContractLifecycleService
from icp_renov_contracts.ui.web_host import UiBridge

from test_contract_events import RecordingOpener
from test_document_generation import GenerationCase
from test_review import FakeCapabilities


class Clock:
    def __init__(self, value: date): self.value = value
    def today(self): return self.value


class WebContractRenewalW3D3Tests(GenerationCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock(date(2026, 10, 2))
        self.review.capability_probe = FakeCapabilities(True, True)
        self.generation = self.service()
        self.lifecycle = ContractLifecycleService(
            self.context.database, self.contracts, self.documents,
            ContractEventRepository(self.context.database), self.context.workspace.root,
            RecordingOpener(), self.clock, lambda: "2026-10-02T10:00:00+00:00",
        )
        self.context = replace(self.context, review=self.review, generation=self.generation, lifecycle=self.lifecycle)
        self.bridge = UiBridge(self.context, lambda _destination: None)

    def _conditions(self, mode: str, rule: str | None = "FIXED", start="2026-01-01", months=12, price="100"):
        current = self.contracts.get_conditions(self.contract.id)
        return replace(current, start_date=start, initial_duration_mode="STANDARD", initial_duration_months=months,
            initial_end_date=None, annual_ht=price, vat_rate="20", renewal_mode=mode,
            renewal_period_months=12 if mode != "NONE" else None,
            non_renewal_notice_days=60 if mode == "TACIT" else None,
            non_renewal_notice_channels=("EMAIL",) if mode == "TACIT" else (),
            internal_alert_days=90 if mode != "NONE" else None,
            renewal_price_rule=rule if mode != "NONE" else None, breach_cure_period_days=15)

    def _signed(self, mode="TACIT", rule="FIXED", start="2026-01-01", months=12, price="100"):
        self.contracts.save_conditions(self.contract.id, self._conditions(mode, rule, start, months, price))
        generated = self.generation.generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id, generated.document.id, "2026-01-02")
        self.bridge.openContract(self.contract.id); self.bridge.setContractStep(self.contract.id, 4)
        return generated

    def _state(self): return self.bridge.contract_workspace_snapshot()
    def _events(self, contract_id=None): return self.lifecycle.history(contract_id or self.contract.id)

    def test_none_has_no_primary_renewal_or_confirmed_event_but_can_prepare_linked_draft(self):
        source = self._signed("NONE")
        state = self._state()
        self.assertEqual(state["documents_d3"]["mode"], "NONE")
        self.assertTrue(state["documents_d3"]["linked_draft_allowed"])
        self.assertEqual(state["documents_d3"]["linked_draft_label"], "Créer un nouveau contrat lié")
        self.assertIsNone(state["documents_d3"]["tacit"])
        self.assertNotIn(ContractEventType.RENEWAL_CONFIRMED, [event.type for event in self._events()])
        self.assertEqual(len(self.lifecycle.revisions(self.contract.id)), 1)
        self.assertTrue(source.pdf_path.is_file())

    def test_manual_modal_cancel_is_side_effect_free_and_same_request_creates_one_linked_draft(self):
        source = self._signed("MANUAL")
        before_events = self._events(); before_docs = self.lifecycle.revisions(self.contract.id)
        state = self._state()
        self.assertEqual(state["documents_d3"]["linked_draft_label"], "Préparer le renouvellement")
        self.assertEqual(self._events(), before_events)
        linked = self.lifecycle.create_linked_draft(self.contract.id, "manual-click")
        duplicate = self.lifecycle.create_linked_draft(self.contract.id, "manual-click")
        self.assertEqual(linked.id, duplicate.id)
        self.assertEqual((linked.status, linked.predecessor_contract_id, linked.number), (ContractStatus.DRAFT, self.contract.id, None))
        self.assertEqual(self.lifecycle.revisions(linked.id), ())
        self.assertFalse(any(event.type is ContractEventType.SIGNATURE_RECORDED for event in self._events(linked.id)))
        self.assertEqual(self._events(), before_events); self.assertEqual(self.lifecycle.revisions(self.contract.id), before_docs)
        self.assertEqual([item.snapshot for item in linked.equipment_items], [item.snapshot for item in self.contracts.get(self.contract.id).equipment_items])
        self.assertTrue(self.contracts.get_conditions(linked.id).start_date)
        self.assertTrue(source.pdf_path.is_file())

    def test_manual_bridge_opens_the_new_draft_without_changing_the_source(self):
        self._signed("MANUAL")
        source_id = self.contract.id; before = self._events(source_id)
        response = self.bridge.prepareLinkedRenewal(source_id, "bridge-click")
        self.assertTrue(response["ok"])
        self.assertEqual(self.bridge.contract_id, response["id"])
        linked = self.contracts.get(response["id"])
        self.assertEqual((linked.status, linked.predecessor_contract_id, linked.number), (ContractStatus.DRAFT, source_id, None))
        self.assertEqual(self._events(source_id), before)
        self.assertEqual(self.lifecycle.revisions(linked.id), ())

    def test_tacit_never_auto_confirms_and_attention_projection_is_python_authoritative(self):
        generated = self._signed("TACIT")
        # The existing operational-signal authority rightly prioritises a missing
        # signed-copy action. Resolve that separate D2 state before asserting the
        # D3 renewal signal.
        self.lifecycle.add_signed_copy(self.contract.id, generated.pdf_path)
        before = self._events()
        self.lifecycle.reconcile_lifecycle(date(2027, 1, 2))
        self.assertEqual(self._events(), before)
        state = self._state(); tacit = state["documents_d3"]["tacit"]
        self.assertTrue(tacit["confirmation_allowed"])
        self.assertEqual(tacit["current_period_display"], "Du 01/01/2026 au 31/12/2026")
        self.assertEqual(tacit["next_period_display"], "Du 01/01/2027 au 31/12/2027")
        row = next(row for row in self.bridge.register.rows() if row.contract_id == self.contract.id)
        self.assertEqual(row.signal.label, "Reconduction à confirmer")

    def test_tacit_fixed_persists_exact_event_updates_current_period_and_leaves_initial_price_untouched(self):
        generated = self._signed("TACIT", "FIXED")
        initial = self.contracts.get_conditions(self.contract.id)
        before_documents = self.lifecycle.revisions(self.contract.id)
        response = self.bridge.confirmTacitRenewal(self.contract.id, "", "")
        self.assertTrue(response["ok"])
        event = next(event for event in self._events() if event.type is ContractEventType.RENEWAL_CONFIRMED)
        self.assertEqual((event.period_start, event.period_end), ("2027-01-01", "2027-12-31"))
        self.assertEqual((event.renewal_annual_ht, event.renewal_vat_rate, event.renewal_vat_amount, event.renewal_annual_ttc), ("100.00", "20.00", "20.00", "120.00"))
        self.assertEqual(self.contracts.get_conditions(self.contract.id), initial)
        self.assertEqual(self.lifecycle.revisions(self.contract.id), before_documents)
        state = self._state()
        self.assertIn("En cours · Du 01/01/2027 au 31/12/2027", state["summary"]["period"])
        self.assertFalse(state["documents_d3"]["tacit"]["confirmation_allowed"])
        self.assertTrue(any(item["label"] == "Reconduction confirmée" and item["effective_display"] == "Du 01/01/2027 au 31/12/2027" for item in state["documents_d1"]["timeline"]))
        self.assertTrue(generated.pdf_path.is_file())

    def test_history_merges_events_and_signed_copy_by_recorded_timestamp_not_business_dates(self):
        self.contracts.save_conditions(self.contract.id, self._conditions("TACIT", "FIXED"))
        generated = self.generation.generate(self.contract.id)
        # These recording timestamps are deliberately distinct from their
        # business-effective dates: signature = 02/01, activation = 01/01,
        # renewal period = 2027. No immutable event is rewritten.
        self.clock.value = date(2025, 12, 20)
        self.lifecycle.now_provider = lambda: "2026-07-11T09:00:00+00:00"
        self.lifecycle.record_signature(self.contract.id, generated.document.id, "2026-01-02")
        self.clock.value = date(2026, 10, 2)
        self.lifecycle.now_provider = lambda: "2026-07-12T09:00:00+00:00"
        self.lifecycle.reconcile_lifecycle(self.clock.value)
        self.lifecycle.now_provider = lambda: "2026-08-01T09:00:00+00:00"
        signed_copy = self.lifecycle.add_signed_copy(self.contract.id, generated.pdf_path)
        self.lifecycle.now_provider = lambda: "2026-10-02T10:00:00+00:00"
        self.bridge.openContract(self.contract.id); self.bridge.setContractStep(self.contract.id, 4)
        self.assertTrue(self.bridge.confirmTacitRenewal(self.contract.id, "", "")["ok"])
        timeline = self._state()["documents_d1"]["timeline"]
        relevant = [item for item in timeline if item["label"] in {"Reconduction confirmée", "Copie signée archivée", "Contrat activé", "Signature enregistrée"}]
        self.assertEqual([item["label"] for item in relevant], ["Reconduction confirmée", "Copie signée archivée", "Contrat activé", "Signature enregistrée"])
        self.assertEqual(relevant[0]["effective_display"], "Du 01/01/2027 au 31/12/2027")
        self.assertEqual(relevant[2]["effective_display"], "01/01/2026")
        self.assertEqual(relevant[3]["effective_display"], "02/01/2026")
        self.assertTrue(relevant[1]["documentary"])
        self.assertEqual(signed_copy.id, self.documents.get(generated.document.id).id)

    def test_tacit_new_price_requires_controlled_values_and_uses_python_calculation(self):
        self._signed("TACIT", "NEW_PRICE_ON_RENEWAL")
        initial = self.contracts.get_conditions(self.contract.id)
        self.assertFalse(self.bridge.confirmTacitRenewal(self.contract.id, "", "")["ok"])
        self.assertFalse(self.bridge.confirmTacitRenewal(self.contract.id, "150", "5.5")["ok"])
        preview = self.bridge.previewTacitRenewal(self.contract.id, "150", "20")
        self.assertEqual(preview["price"], {"annual_ht": "150,00", "vat_rate": "20,00", "vat_amount": "30,00", "annual_ttc": "180,00"})
        self.assertTrue(self.bridge.confirmTacitRenewal(self.contract.id, "150", "20")["ok"])
        event = next(event for event in self._events() if event.type is ContractEventType.RENEWAL_CONFIRMED)
        self.assertEqual((event.renewal_annual_ht, event.renewal_vat_rate, event.renewal_vat_amount, event.renewal_annual_ttc), ("150.00", "20.00", "30.00", "180.00"))
        self.assertEqual(self.contracts.get_conditions(self.contract.id), initial)

    def test_tacit_bridge_rejects_confirmation_before_authoritative_attention_date(self):
        self._signed("TACIT", "FIXED")
        self.clock.value = date(2026, 1, 3)
        before = self._events()
        response = self.bridge.confirmTacitRenewal(self.contract.id, "", "")
        self.assertFalse(response["ok"])
        self.assertIn("pas encore à confirmer", response["message"])
        self.assertEqual(self._events(), before)
        self.assertEqual(self.lifecycle.lifecycle_projection(self.contract.id).period.end, date(2026, 12, 31))

    def test_tacit_persistence_failure_preserves_period_history_and_no_success(self):
        self._signed("TACIT", "FIXED")
        before = self._events()
        with patch("icp_renov_contracts.services.contract_events.ContractEventRepository.insert", side_effect=sqlite3.IntegrityError("forced")):
            response = self.bridge.confirmTacitRenewal(self.contract.id, "", "")
        self.assertFalse(response["ok"])
        self.assertIn("n’a pas été enregistrée", response["message"])
        self.assertEqual(self._events(), before)
        self.assertEqual(self.lifecycle.lifecycle_projection(self.contract.id).period.end, date(2026, 12, 31))
        self.assertIsNone(self.bridge.contract_documents_feedback)

    def test_tacit_period_idempotency_allows_a_later_distinct_period_but_not_indexed(self):
        self._signed("TACIT", "FIXED", start="2024-02-29", months=12)
        first = self.lifecycle.confirm_renewal(self.contract.id)
        second = self.lifecycle.confirm_renewal(self.contract.id)
        self.assertNotEqual(first.period_start, second.period_start)
        self.assertEqual(first.period_end, standard_end_date(first.period_start, 12))
        self.assertEqual(second.period_start, "2026-02-28")
        self.assertEqual(second.period_end, standard_end_date(second.period_start, 12))
        self.contract = self.contracts.create_draft(); self.complete(renewal="TACIT")
        with self.assertRaises(ContractConditionsValidationError):
            self.contracts.save_conditions(self.contract.id, self._conditions("TACIT", "INDEXED"))

    def test_duplicate_same_tacit_period_is_rejected_by_database_authority(self):
        self._signed("TACIT")
        first = self.lifecycle.confirm_renewal(self.contract.id)
        with self.context.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO contract_events(id,contract_id,type,occurred_at,period_start,period_end,renewal_annual_ht,renewal_vat_rate,renewal_vat_amount,renewal_annual_ttc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), self.contract.id, "RENEWAL_CONFIRMED", "2026-10-02T10:00:01+00:00",
                     first.period_start, first.period_end, first.renewal_annual_ht, first.renewal_vat_rate,
                     first.renewal_vat_amount, first.renewal_annual_ttc),
                )
        self.assertEqual(sum(event.type is ContractEventType.RENEWAL_CONFIRMED for event in self._events()), 1)

    def test_web_surface_has_only_d3_renewal_controls_and_python_modal_bridge(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renderContractDocuments"); end = js.index("function renderContractReview", start)
        d3 = js[start:end]
        for text in ("Préparer le renouvellement", "Créer un nouveau contrat lié", "Confirmer la reconduction", "previewTacitRenewal"):
            self.assertIn(text, js)
        host = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui" / "web_host.py").read_text(encoding="utf-8")
        self.assertIn("Reconduction confirmée", host)
        for forbidden in ("Programmer une résiliation", "Enregistrer une fin de contrat", "Créer une fiche d’intervention", "INDEXED"):
            self.assertNotIn(forbidden, d3)
        engine = QJSEngine(); engine.evaluate("function esc(value){return String(value ?? '');} var contractDocumentsError='';")
        evaluated = engine.evaluate(d3); self.assertFalse(evaluated.isError(), evaluated.toString())
        base = {"documents_d1": {"feedback": None, "revisions": [], "timeline": [], "correction_allowed": False, "send_allowed": False}, "documents_d2": {"signature_allowed": False}, "documents_d3": {"mode": "NONE", "linked_draft_allowed": False, "tacit": None}}
        html = engine.evaluate(f"renderContractDocuments({json.dumps(base)})").toString()
        self.assertNotIn("Préparer le renouvellement", html); self.assertNotIn("Confirmer la reconduction", html)


if __name__ == "__main__":
    unittest.main()
