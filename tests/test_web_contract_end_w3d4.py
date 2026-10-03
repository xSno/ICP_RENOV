from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import unittest
import uuid

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.domain import ContractEvent, ContractEventType, ContractStatus
from icp_renov_contracts.errors import ContractLifecycleError
from icp_renov_contracts.services import ContractLifecycleService, ContractRegisterFilter
from icp_renov_contracts.repositories import ContractEventRepository
from icp_renov_contracts.ui.web_host import UiBridge

from test_contract_events import RecordingOpener
from test_document_generation import GenerationCase, REQUIRED_COMPANY_FIELDS
from test_review import FakeCapabilities


class Clock:
    def __init__(self, value: date): self.value = value
    def today(self): return self.value


class OccurrenceClock:
    def __init__(self): self.value = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    def __call__(self):
        recorded = self.value.isoformat()
        self.value += timedelta(seconds=1)
        return recorded


class WebContractEndW3D4Tests(GenerationCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock(date(2026, 10, 2))
        self.occurrence_clock = OccurrenceClock()
        self.review.capability_probe = FakeCapabilities(True, True)
        self.generation = self.service()
        self.lifecycle = ContractLifecycleService(
            self.context.database, self.contracts, self.documents,
            ContractEventRepository(self.context.database), self.context.workspace.root,
            RecordingOpener(), self.clock, self.occurrence_clock,
        )
        self.context = replace(self.context, review=self.review, generation=self.generation, lifecycle=self.lifecycle)
        self.bridge = UiBridge(self.context, lambda _destination: None)

    def _conditions(self, mode="TACIT", start="2026-01-01", months=12):
        current = self.contracts.get_conditions(self.contract.id)
        return replace(
            current, start_date=start, initial_duration_mode="STANDARD", initial_duration_months=months,
            initial_end_date=None, annual_ht="100", vat_rate="20", renewal_mode=mode,
            renewal_period_months=12 if mode != "NONE" else None,
            non_renewal_notice_days=60 if mode == "TACIT" else None,
            non_renewal_notice_channels=("EMAIL",) if mode == "TACIT" else (),
            internal_alert_days=90 if mode != "NONE" else None,
            renewal_price_rule="FIXED" if mode != "NONE" else None,
            breach_cure_period_days=15,
        )

    def _signed(self, mode="TACIT"):
        self.contracts.save_conditions(self.contract.id, self._conditions(mode))
        generated = self.generation.generate(self.contract.id)
        self.lifecycle.record_signature(self.contract.id, generated.document.id, "2026-01-02")
        self.bridge.openContract(self.contract.id)
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        return generated

    def _enable_generation_for_current_contract(self):
        version = self.contracts.selected_template_version(self.contract.id)
        source = next((Path(__file__).parents[1] / "templates").glob("*HABITATION*.docx"))
        relpath, digest = self.store.import_source(source, version.id)
        self.catalog.set_generation_metadata(version.id, relpath, digest, REQUIRED_COMPANY_FIELDS)

    def _state(self): return self.bridge.contract_workspace_snapshot()
    def _events(self): return self.lifecycle.history(self.contract.id)

    def test_tacit_nonrenewal_is_external_record_only_and_blocks_same_period_renewal(self):
        generated = self._signed("TACIT")
        before_documents = self.lifecycle.revisions(self.contract.id)
        state = self._state()
        notice = state["documents_d4"]["nonrenewal"]
        self.assertTrue(notice["allowed"])
        self.assertEqual(notice["channels"], ["E-mail"])
        self.assertEqual(notice["current_period_display"], "Du 01/01/2026 au 31/12/2026")
        self.assertEqual((notice["today"], notice["notice_days"]), ("2026-10-02", 60))
        response = self.bridge.recordContractNonRenewal(self.contract.id, "2026-10-01", "Courrier transmis")
        self.assertTrue(response["ok"])
        event = next(item for item in self._events() if item.type is ContractEventType.RENEWAL_NOTICE_RECORDED)
        self.assertEqual((event.effective_date, event.note), ("2026-10-01", "Courrier transmis"))
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.ACTIVE)
        self.assertEqual(self.lifecycle.revisions(self.contract.id), before_documents)
        self.assertTrue(generated.pdf_path.is_file())
        state = self._state()
        self.assertTrue(state["documents_d4"]["nonrenewal"]["recorded"])
        self.assertIsNone(state["documents_d3"]["tacit"])
        self.assertTrue(any(item["label"] == "Non-renouvellement enregistré" for item in state["documents_d1"]["timeline"]))
        with self.assertRaises(ContractLifecycleError): self.lifecycle.confirm_renewal(self.contract.id)

    def test_controlled_termination_only_schedules_and_register_signal_is_information(self):
        generated = self._signed("MANUAL")
        self.lifecycle.add_signed_copy(self.contract.id, generated.pdf_path)
        state = self._state()
        termination = state["documents_d4"]["termination"]
        self.assertTrue(termination["allowed"])
        self.assertEqual(termination["reasons"], [{"id": "BREACH", "label": "Manquement"}])
        self.assertEqual(termination["scheduled_effective_display"], "")
        self.assertFalse(self.bridge.scheduleContractTermination(self.contract.id, "2026-11-01", "UNCONTROLLED", "", "")["ok"])
        response = self.bridge.scheduleContractTermination(self.contract.id, "2026-11-01", "BREACH", "2026-10-02", "Fait extérieur")
        self.assertTrue(response["ok"])
        event = next(item for item in self._events() if item.type is ContractEventType.TERMINATION_SCHEDULED)
        self.assertEqual((event.effective_date, event.notification_date, event.reason_code, event.reason_text, event.note), ("2026-11-01", "2026-10-02", "BREACH", "Manquement", "Fait extérieur"))
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.ACTIVE)
        state = self._state()
        scheduled = state["documents_d4"]["termination"]
        self.assertTrue(scheduled["scheduled"])
        self.assertEqual(scheduled["scheduled_effective_display"], "01/11/2026")
        row = next(row for row in self.bridge.register.rows() if row.contract_id == self.contract.id)
        self.assertEqual(row.signal.kind.value, "INFORMATION")
        self.assertIn("Résiliation programmée", row.signal.label)
        rows = self.bridge.register.rows()
        self.assertEqual(self.bridge.register.action_count(rows), 0)
        self.assertIn(self.contract.id, [row.contract_id for row in self.bridge.register.filter_rows(rows, ContractRegisterFilter.ALL, "")])
        self.assertNotIn(self.contract.id, [row.contract_id for row in self.bridge.register.filter_rows(rows, ContractRegisterFilter.ACTIONS, "")])

    def test_due_termination_precedes_expiration_and_preserves_history(self):
        self._signed("NONE")
        self.assertTrue(self.bridge.scheduleContractTermination(self.contract.id, "2026-10-02", "BREACH", "", "")["ok"])
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.TERMINATED)
        types = [item.type for item in self._events()]
        self.assertEqual(types.count(ContractEventType.TERMINATION_SCHEDULED), 1)
        self.assertEqual(types.count(ContractEventType.TERMINATED), 1)
        self.lifecycle.reconcile_lifecycle(date(2027, 1, 2))
        self.assertNotIn(ContractEventType.EXPIRED, [item.type for item in self._events()])
        timeline = self._state()["documents_d1"]["timeline"]
        labels = [item["label"] for item in timeline]
        self.assertIn("Résiliation programmée", labels)
        self.assertIn("Contrat résilié", labels)

    def test_terminal_occurrences_use_recording_clock_and_keep_honest_tie_order(self):
        self._signed("NONE")
        existing = next(item for item in self._events() if item.type is ContractEventType.ACTIVATED)
        self.lifecycle.now_provider = lambda: existing.occurred_at
        before = self._events()
        self.assertTrue(self.bridge.scheduleContractTermination(self.contract.id, "2026-10-02", "BREACH", "", "")["ok"])
        events = self._events()
        scheduled = next(item for item in events if item.type is ContractEventType.TERMINATION_SCHEDULED)
        terminated = next(item for item in events if item.type is ContractEventType.TERMINATED)
        self.assertEqual((scheduled.occurred_at, terminated.occurred_at), (existing.occurred_at, existing.occurred_at))
        self.assertEqual(tuple(item for item in events if item.id in {event.id for event in before}), before)
        tied = [existing, scheduled, terminated]
        expected = [
            {
                ContractEventType.ACTIVATED: "Contrat activé",
                ContractEventType.TERMINATION_SCHEDULED: "Résiliation programmée",
                ContractEventType.TERMINATED: "Contrat résilié",
            }[event.type]
            for event in sorted(tied, key=lambda item: (item.occurred_at, item.id), reverse=True)
        ]
        actual = [
            item["label"] for item in self._state()["documents_d1"]["timeline"]
            if item["label"] in expected
        ]
        self.assertEqual(actual, expected)

        self.contract = self.contracts.create_draft()
        self.complete(renewal="NONE")
        self._enable_generation_for_current_contract()
        self.contracts.save_conditions(self.contract.id, self._conditions("NONE"))
        generated = self.generation.generate(self.contract.id)
        self.bridge.openContract(self.contract.id)
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        future = ContractEvent(str(uuid.uuid4()), self.contract.id, ContractEventType.CONTRACT_SENT,
                               "2099-01-01T00:00:00+00:00", "2026-10-02", generated.document.id)
        with self.context.database.transaction() as connection:
            ContractEventRepository.insert(connection, future)
        before_future = self._events()
        actual_recorded_at = "2026-10-02T12:00:00+00:00"
        self.lifecycle.now_provider = lambda: actual_recorded_at
        self.assertTrue(self.bridge.abandonContract(self.contract.id)["ok"])
        abandoned = next(item for item in self._events() if item.type is ContractEventType.ABANDONED)
        self.assertEqual(abandoned.occurred_at, actual_recorded_at)
        self.assertEqual(tuple(item for item in self._events() if item.id in {event.id for event in before_future}), before_future)
        self.assertIn("Contrat abandonné", [item["label"] for item in self._state()["documents_d1"]["timeline"]])

    def test_expiration_is_once_and_tacit_requires_explicit_nonrenewal(self):
        self._signed("NONE")
        self.lifecycle.reconcile_lifecycle(date(2026, 12, 31))
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.EXPIRED)
        self.lifecycle.reconcile_lifecycle(date(2027, 1, 1))
        self.assertEqual(sum(item.type is ContractEventType.EXPIRED for item in self._events()), 1)
        labels = [item["label"] for item in self._state()["documents_d1"]["timeline"]]
        self.assertIn("Contrat expiré", labels)

        self.contract = self.contracts.create_draft()
        self.complete(renewal="TACIT")
        self._enable_generation_for_current_contract()
        self._signed("TACIT")
        self.lifecycle.reconcile_lifecycle(date(2027, 1, 1))
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.ACTIVE)
        self.assertFalse(any(item.type is ContractEventType.EXPIRED for item in self._events()))

    def test_abandonment_is_available_without_revision_and_preserves_to_sign_revision(self):
        draft = self.contracts.create_draft()
        self.bridge.openContract(draft.id)
        self.assertTrue(self.bridge.setContractStep(draft.id, 4)["ok"])
        state = self._state()
        self.assertTrue(state["documents_d4"]["abandon_allowed"])
        self.assertTrue(self.bridge.abandonContract(draft.id)["ok"])
        self.assertIs(self.contracts.get(draft.id).status, ContractStatus.ABANDONED)
        self.assertFalse(self.bridge.abandonContract(draft.id)["ok"])

        self.contract = self.contracts.create_draft()
        self.complete(renewal="NONE")
        self._enable_generation_for_current_contract()
        self.contracts.save_conditions(self.contract.id, self._conditions("NONE"))
        generated = self.generation.generate(self.contract.id)
        self.bridge.openContract(self.contract.id)
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.TO_SIGN)
        self.assertTrue(self.bridge.abandonContract(self.contract.id)["ok"])
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.ABANDONED)
        self.assertEqual(len(self.lifecycle.revisions(self.contract.id)), 1)
        self.assertTrue(generated.pdf_path.is_file())
        labels = [item["label"] for item in self._state()["documents_d1"]["timeline"]]
        self.assertIn("Contrat abandonné", labels)

    def test_d4_web_surface_uses_only_controlled_routes_and_terminal_copy(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        path = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js"
        js = path.read_text(encoding="utf-8")
        for copy in ("Enregistrer une fin de contrat", "Programmer une résiliation", "Abandonner le contrat", "Aucun message ne sera envoyé"):
            self.assertIn(copy, js)
        self.assertIn("scheduleContractTermination", js)
        self.assertNotIn("Résilier maintenant", js)
        self.assertNotIn("jour(s)", js)
        helper_start = js.index("function formatFrenchDate")
        helper_end = js.index("function openContractFile", helper_start)
        helpers = js[helper_start:helper_end]
        engine = QJSEngine()
        engine.evaluate("function esc(value){return String(value ?? '');}")
        evaluated_helpers = engine.evaluate(helpers)
        self.assertFalse(evaluated_helpers.isError(), evaluated_helpers.toString())
        self.assertEqual(engine.evaluate("formatFrenchDate('2026-08-21')").toString(), "21/08/2026")
        self.assertEqual(engine.evaluate("parseFrenchDate('21/08/2026')").toString(), "2026-08-21")
        self.assertEqual(engine.evaluate("parseFrenchDate('08/21/2026')").toString(), "")
        self.assertEqual(engine.evaluate("formatDayCount(1)").toString(), "1 jour")
        self.assertEqual(engine.evaluate("formatDayCount(60)").toString(), "60 jours")
        start = js.index("function renderContractDocuments")
        end = js.index("function renderContractReview", start)
        engine.evaluate("function esc(value){return String(value ?? '');} var contractDocumentsError='';")
        evaluated = engine.evaluate(js[start:end])
        self.assertFalse(evaluated.isError(), evaluated.toString())
        base = {"documents_d1": {"feedback": None, "revisions": [], "timeline": [], "correction_allowed": False, "send_allowed": False}, "documents_d2": {"signature_allowed": False}, "documents_d3": {"mode": "NONE", "linked_draft_allowed": False, "tacit": None}, "documents_d4": {"nonrenewal": {"allowed": True, "recorded": False, "current_period_display": "Du 01/01/2026 au 31/12/2026", "period_end_display": "31/12/2026"}, "termination": {"allowed": True, "scheduled": True, "scheduled_effective_display": "01/11/2026"}, "abandon_allowed": False}}
        html = engine.evaluate("renderContractDocuments(" + json.dumps(base) + ")").toString()
        self.assertIn("Enregistrer une fin de contrat", html)
        self.assertIn("Programmer une résiliation", html)
        self.assertIn("Fin prévue le 01/11/2026", html)
        self.assertNotIn("Abandonner le contrat", html)

    def test_register_frontend_pluralizes_action_count(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        path = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "app.js"
        js = path.read_text(encoding="utf-8")
        start = js.index("function actionCountLabel")
        end = js.index("\n}\n", start) + 3
        engine = QJSEngine()
        evaluated = engine.evaluate(js[start:end])
        self.assertFalse(evaluated.isError(), evaluated.toString())
        self.assertEqual(engine.evaluate("actionCountLabel(1)").toString(), "1 action à traiter")
        self.assertEqual(engine.evaluate("actionCountLabel(2)").toString(), "2 actions à traiter")


if __name__ == "__main__":
    unittest.main()
