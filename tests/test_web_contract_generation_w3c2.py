from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import Barrier, Lock, Thread
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

from icp_renov_contracts.documents import ProductionDocxRenderer
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.domain import (
    AnnualNumberingPolicy, ContractEventType, ContractStatus, NumberFormatMode,
    NumberingSettings, TemplateVersionStatus,
)
from icp_renov_contracts.repositories import ContractDocumentRepository
from icp_renov_contracts.services import PersistedContractNumberAllocator
from icp_renov_contracts.ui.web_host import UiBridge

from test_document_generation import FakeConverter, FailingRenderer, GenerationCase
from test_review import FakeCapabilities


class InterleavingRenderer:
    def __init__(self):
        self.barrier = Barrier(2)
        self.lock = Lock()
        self.contexts = []
        self.calls = 0
        self.delegate = ProductionDocxRenderer()

    def render(self, source, output, context, workdir):
        with self.lock:
            self.calls += 1
            call = self.calls
            self.contexts.append((context["contract"]["number"], context["document"]["revision"]))
        if call <= 2:
            self.barrier.wait(timeout=10)
        self.delegate.render(source, output, context, workdir)


class SimulatedProcessTermination(BaseException):
    pass


class WebContractGenerationW3C2Tests(GenerationCase):
    def setUp(self):
        super().setUp()
        self.review.capability_probe = FakeCapabilities(True, True)
        self.generation = self.service()
        self.default_renderer = self.generation.renderer
        self.default_converter = self.generation.converter
        self.context = replace(self.context, review=self.review, generation=self.generation)
        self.states = []
        self.bridge = UiBridge(self.context, lambda _destination: None)
        self.bridge.stateChanged.connect(self.states.append)
        self.bridge.openContract(self.contract.id)
        self.bridge.setContractStep(self.contract.id, 3)

    def official(self):
        return self.bridge.contract_workspace_snapshot()["official_generation"]

    def event_types(self):
        with self.context.database.connection() as connection:
            return [row[0] for row in connection.execute(
                "SELECT type FROM contract_events WHERE contract_id=? ORDER BY occurred_at,id",
                (self.contract.id,),
            )]

    def second_complete_contract(self):
        original = self.contracts.get(self.contract.id)
        second = self.contracts.create_draft()
        self.contracts.select_client(second.id, original.client_source_id)
        self.contracts.select_site(second.id, original.site_source_id)
        for item in original.equipment_items:
            self.contracts.select_equipment(second.id, item.source_equipment_id)
        self.contracts.change_regime(second.id, original.regime.value)
        self.contracts.select_template_version(second.id, self.version.id)
        self.contracts.save_conditions(second.id, self.contracts.get_conditions(self.contract.id))
        return self.contracts.get(second.id)

    def persisted_allocator(self):
        self.context.numbering.save(NumberingSettings(
            NumberFormatMode.PREFIX_COUNTER, "SYNTH-S5", 4,
            AnnualNumberingPolicy.CONTINUOUS, 1, 1,
        ))
        return PersistedContractNumberAllocator(self.context.numbering)

    @staticmethod
    def run_parallel(attempts):
        outcomes = {}

        def run(key, service, contract_id):
            try:
                outcomes[key] = service.generate(contract_id)
            except Exception as error:
                outcomes[key] = error

        threads = [Thread(target=run, args=attempt, daemon=True) for attempt in attempts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        if any(thread.is_alive() for thread in threads):
            raise AssertionError("generation contention did not finish")
        return outcomes

    def test_cta_authority_requires_business_and_generation_readiness(self):
        ready = self.official()
        self.assertTrue(ready["allowed"])
        self.assertEqual(ready["preview_number"], "SYNTH-S5-0001")
        self.assertEqual(ready["next_revision"], "R01")

        conditions = self.contracts.get_conditions(self.contract.id)
        self.contracts.save_conditions(self.contract.id, replace(conditions, visits_per_year=None))
        self.assertFalse(self.official()["allowed"])
        self.contracts.save_conditions(self.contract.id, conditions)

        self.generation.converter = FakeConverter("unavailable")
        self.assertFalse(self.official()["allowed"])

    def test_confirmation_copy_is_local_and_has_zero_business_side_effects(self):
        before_contract = self.contracts.get(self.contract.id)
        before_documents = self.documents.list_for_contract(self.contract.id)
        before_events = self.event_types()
        self.bridge.contract_workspace_snapshot()
        self.assertEqual(self.contracts.get(self.contract.id), before_contract)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), before_documents)
        self.assertEqual(self.event_types(), before_events)

        js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")
        opening = js[js.index("function openGenerationConfirmation"):js.index("function renderContractReview")]
        self.assertNotIn("bridge.generateOfficialContract", opening)
        confirmation = js[js.index("function confirmOfficialGeneration"):js.index("function openGenerationConfirmation")]
        self.assertIn("bridge.generateOfficialContract", confirmation)
        for copy in ("Annuler", "Générer le DOCX et le PDF", "Numéro prévu", "À signer", "Si la génération échoue"):
            self.assertIn(copy, opening)

    def test_complete_success_creates_exact_r01_snapshot_event_number_and_to_sign(self):
        result = self.bridge.generateOfficialContract(self.contract.id)
        self.assertTrue(result["ok"])
        self.assertEqual((result["contract_number"], result["revision"]), ("SYNTH-S5-0001", "R01"))
        contract = self.contracts.get(self.contract.id)
        self.assertEqual(contract.number, "SYNTH-S5-0001")
        self.assertIs(contract.status, ContractStatus.TO_SIGN)
        documents = self.documents.list_for_contract(self.contract.id)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].template_version_id, self.version.id)
        self.assertEqual(documents[0].snapshot["template"]["version_id"], self.version.id)
        self.assertTrue((self.context.workspace.root / documents[0].docx_relpath).is_file())
        self.assertTrue((self.context.workspace.root / documents[0].pdf_relpath).is_file())
        self.assertEqual(self.event_types().count(ContractEventType.DOCUMENT_GENERATED.value), 1)
        snapshot = self.bridge.contract_workspace_snapshot()
        self.assertEqual(snapshot["contract"]["status_label"], "À signer")
        self.assertEqual(snapshot["contract"]["number"], "SYNTH-S5-0001")
        self.assertEqual(snapshot["official_generation"]["feedback"]["kind"], "success")

    def test_server_preflight_rejects_stale_ready_state(self):
        self.assertTrue(self.official()["allowed"])
        conditions = self.contracts.get_conditions(self.contract.id)
        self.contracts.save_conditions(self.contract.id, replace(conditions, visits_per_year=None))
        result = self.bridge.generateOfficialContract(self.contract.id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "incomplete")
        self.assertIn("conservées", result["guarantee"])
        self.assertIsNone(self.contracts.get(self.contract.id).number)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), ())

    def test_docx_pdf_postflight_and_publication_failures_are_non_official(self):
        cases = (
            (FailingRenderer(), FakeConverter(), "render_failure"),
            (self.default_renderer, FakeConverter("failure"), "converter_failure"),
            (self.default_renderer, FakeConverter("invalid"), "invalid_pdf"),
        )
        for renderer, converter, code in cases:
            with self.subTest(code=code):
                self.generation.renderer = renderer
                self.generation.converter = converter
                result = self.bridge.generateOfficialContract(self.contract.id)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], code)
                self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.DRAFT)
                self.assertIsNone(self.contracts.get(self.contract.id).number)
                self.assertEqual(self.documents.list_for_contract(self.contract.id), ())
                self.assertEqual(self.allocator.consumed(), 0)
        self.generation.renderer = self.default_renderer
        self.generation.converter = self.default_converter
        with patch.object(ContractDocumentRepository, "insert", side_effect=sqlite3.OperationalError("db")):
            result = self.bridge.generateOfficialContract(self.contract.id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "publication_failure")
        self.assertEqual(self.documents.list_for_contract(self.contract.id), ())
        self.assertEqual(self.allocator.consumed(), 0)

    def test_existing_revision_is_never_overwritten_by_later_failure(self):
        first = self.generation.generate(self.contract.id)
        before_docx = first.docx_path.read_bytes()
        self.context.lifecycle.reopen_for_correction(self.contract.id)
        self.generation.converter = FakeConverter("failure")
        result = self.bridge.generateOfficialContract(self.contract.id)
        self.assertFalse(result["ok"])
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)], ["R01"])
        self.assertEqual(first.docx_path.read_bytes(), before_docx)
        self.assertEqual(self.contracts.get(self.contract.id).number, first.contract_number)

    def test_duplicate_unavailable_template_and_locked_requests_are_rejected(self):
        with self.generation._attempt_lock:
            self.generation._active_contracts.add(self.contract.id)
        try:
            duplicate = self.bridge.generateOfficialContract(self.contract.id)
        finally:
            with self.generation._attempt_lock:
                self.generation._active_contracts.discard(self.contract.id)
        self.assertEqual(duplicate["code"], "generation_in_progress")
        self.assertEqual(self.documents.list_for_contract(self.contract.id), ())

        self.catalog.update_status(self.version.id, TemplateVersionStatus.ARCHIVED)
        unavailable = self.bridge.generateOfficialContract(self.contract.id)
        self.assertFalse(unavailable["ok"])
        self.assertIn(unavailable["code"], {"incomplete", "template_incompatible"})
        self.assertEqual(self.documents.list_for_contract(self.contract.id), ())
        self.catalog.update_status(self.version.id, TemplateVersionStatus.AVAILABLE)

        self.assertTrue(self.bridge.generateOfficialContract(self.contract.id)["ok"])
        locked = self.bridge.generateOfficialContract(self.contract.id)
        self.assertEqual(locked["code"], "status")
        self.assertEqual(len(self.documents.list_for_contract(self.contract.id)), 1)
        self.assertTrue(self.bridge.setContractStep(self.contract.id, 4)["ok"])

    def test_frontend_ready_disabled_success_and_error_states_are_bounded(self):
        application = QCoreApplication.instance() or QCoreApplication([])
        self.assertIsNotNone(application)
        root = Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web"
        js = (root / "contract-workspace.js").read_text(encoding="utf-8")
        start = js.index("function renderContractReview")
        end = js.index("function renderContractConditions", start)
        engine = QJSEngine()
        engine.evaluate("function esc(value){return String(value ?? '');} var contractSaveFailure=''; var contractGenerationRunning=false;")
        evaluated = engine.evaluate(js[start:end]); self.assertFalse(evaluated.isError(), evaluated.toString())

        ready = engine.evaluate("renderContractReview(" + json.dumps(self.bridge.contract_workspace_snapshot()) + ")").toString()
        self.assertIn('class="primary review-generation-action"  onclick=', ready)
        self.assertNotIn('review-generation-action" disabled', ready)

        self.generation.renderer = FailingRenderer()
        self.bridge.generateOfficialContract(self.contract.id)
        failed = engine.evaluate("renderContractReview(" + json.dumps(self.bridge.contract_workspace_snapshot()) + ")").toString()
        self.assertIn('generation-result error', failed)
        self.assertIn('Aucune nouvelle révision', failed)


    def test_distinct_contracts_stale_number_aborts_then_rerenders_with_atomic_claim(self):
        second = self.second_complete_contract()
        renderer = InterleavingRenderer()
        allocator = self.persisted_allocator()
        outcomes = self.run_parallel((
            ("first", self.service(renderer=renderer, allocator=allocator), self.contract.id),
            ("second", self.service(renderer=renderer, allocator=allocator), second.id),
        ))
        successes = {key: value for key, value in outcomes.items() if not isinstance(value, Exception)}
        failures = {key: value for key, value in outcomes.items() if isinstance(value, Exception)}
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        stale = next(iter(failures.values()))
        self.assertIsInstance(stale, DocumentGenerationError)
        self.assertEqual(stale.code, "number_changed")
        self.assertEqual(renderer.contexts[:2], [("SYNTH-S5-0001", "R01")] * 2)
        self.assertEqual(self.context.numbering.get().next_counter, 2)

        loser_key = next(iter(failures))
        loser_id = self.contract.id if loser_key == "first" else second.id
        retried = self.service(renderer=renderer, allocator=allocator).generate(loser_id)
        self.assertEqual(retried.contract_number, "SYNTH-S5-0002")
        self.assertEqual(retried.document.revision, "R01")
        self.assertEqual(renderer.contexts[-1], ("SYNTH-S5-0002", "R01"))
        self.assertEqual(self.context.numbering.get().next_counter, 3)

        results = [*successes.values(), retried]
        for result in results:
            persisted = self.documents.get(result.document.id)
            self.assertIsNotNone(persisted)
            self.assertEqual(result.contract_number, self.contracts.get(result.document.contract_id).number)
            self.assertEqual(persisted.snapshot["contract"]["number"], result.contract_number)
            self.assertEqual(persisted.snapshot["document"]["revision"], persisted.revision)
            self.assertEqual(Path(persisted.docx_relpath).name, f"{result.contract_number}_{persisted.revision}.docx")
            self.assertEqual(Path(persisted.pdf_relpath).name, f"{result.contract_number}_{persisted.revision}.pdf")
            rendered = self.docx_text(result.docx_path)
            self.assertIn(result.contract_number, rendered)
            self.assertIn(persisted.revision, rendered)

    def test_same_contract_stale_r01_attempt_cannot_publish_as_another_revision(self):
        renderer = InterleavingRenderer()
        allocator = self.persisted_allocator()
        outcomes = self.run_parallel((
            ("a", self.service(renderer=renderer, allocator=allocator), self.contract.id),
            ("b", self.service(renderer=renderer, allocator=allocator), self.contract.id),
        ))
        successes = [value for value in outcomes.values() if not isinstance(value, Exception)]
        failures = [value for value in outcomes.values() if isinstance(value, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], DocumentGenerationError)
        self.assertIn(failures[0].code, {"collision", "status"})
        self.assertEqual(renderer.contexts[:2], [("SYNTH-S5-0001", "R01")] * 2)
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)], ["R01"])
        self.assertEqual(self.context.numbering.get().next_counter, 2)

        self.context.lifecycle.reopen_for_correction(self.contract.id)
        revision_two = self.service(renderer=renderer, allocator=allocator).generate(self.contract.id)
        self.assertEqual(revision_two.contract_number, "SYNTH-S5-0001")
        self.assertEqual(revision_two.document.revision, "R02")
        self.assertEqual(renderer.contexts[-1], ("SYNTH-S5-0001", "R02"))
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)], ["R01", "R02"])
        self.assertEqual(self.context.numbering.get().next_counter, 2)

    def test_interrupted_r01_orphans_are_reconciled_and_retry_publishes_once(self):
        allocator = self.persisted_allocator()
        service = self.service(allocator=allocator)
        with patch.object(ContractDocumentRepository, "insert", side_effect=SimulatedProcessTermination):
            with self.assertRaises(SimulatedProcessTermination):
                service.generate(self.contract.id)

        folder = self.context.workspace.root / "documents" / "contracts" / self.contract.id / "R01"
        orphan_docx = folder / "SYNTH-S5-0001_R01.docx"
        orphan_pdf = folder / "SYNTH-S5-0001_R01.pdf"
        self.assertTrue(orphan_docx.is_file())
        self.assertTrue(orphan_pdf.is_file())
        self.assertIsNone(self.contracts.get(self.contract.id).number)
        self.assertIs(self.contracts.get(self.contract.id).status, ContractStatus.DRAFT)
        self.assertEqual(self.documents.list_for_contract(self.contract.id), ())
        self.assertNotIn(ContractEventType.DOCUMENT_GENERATED.value, self.event_types())
        self.assertEqual(self.context.numbering.get().next_counter, 1)

        result = self.service(allocator=allocator).generate(self.contract.id)
        persisted = self.documents.get(result.document.id)
        self.assertEqual((result.contract_number, persisted.revision), ("SYNTH-S5-0001", "R01"))
        self.assertEqual(len(self.documents.list_for_contract(self.contract.id)), 1)
        self.assertEqual(self.event_types().count(ContractEventType.DOCUMENT_GENERATED.value), 1)
        self.assertEqual(self.context.numbering.get().next_counter, 2)
        self.assertEqual(persisted.snapshot["contract"]["number"], result.contract_number)
        self.assertEqual(persisted.snapshot["document"]["revision"], persisted.revision)
        self.assertEqual(Path(persisted.docx_relpath).name, "SYNTH-S5-0001_R01.docx")
        self.assertEqual(Path(persisted.pdf_relpath).name, "SYNTH-S5-0001_R01.pdf")
        self.assertIn(result.contract_number, self.docx_text(result.docx_path))
        self.assertIn(persisted.revision, self.docx_text(result.docx_path))

    def test_authoritative_r01_is_preserved_while_interrupted_r02_is_reconciled(self):
        allocator = self.persisted_allocator()
        service = self.service(allocator=allocator)
        first = service.generate(self.contract.id)
        r01_docx = first.docx_path.read_bytes()
        r01_pdf = first.pdf_path.read_bytes()
        authority_probe = self.context.workspace.root / "tmp" / "authority-check"
        authority_probe.mkdir(parents=True)
        with self.context.database.transaction() as connection:
            with self.assertRaises(DocumentGenerationError) as collision:
                service._reconcile_interrupted_target(
                    connection, first.docx_path, first.document.docx_relpath, authority_probe
                )
        self.assertEqual(collision.exception.code, "collision")
        self.assertEqual(first.docx_path.read_bytes(), r01_docx)
        authority_probe.rmdir()
        self.context.lifecycle.reopen_for_correction(self.contract.id)

        with patch.object(ContractDocumentRepository, "insert", side_effect=SimulatedProcessTermination):
            with self.assertRaises(SimulatedProcessTermination):
                self.service(allocator=allocator).generate(self.contract.id)
        folder = self.context.workspace.root / "documents" / "contracts" / self.contract.id / "R02"
        self.assertTrue((folder / "SYNTH-S5-0001_R02.docx").is_file())
        self.assertTrue((folder / "SYNTH-S5-0001_R02.pdf").is_file())
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)], ["R01"])
        self.assertEqual(first.docx_path.read_bytes(), r01_docx)
        self.assertEqual(first.pdf_path.read_bytes(), r01_pdf)
        self.assertEqual(self.context.numbering.get().next_counter, 2)

        second = self.service(allocator=allocator).generate(self.contract.id)
        self.assertEqual((second.contract_number, second.document.revision), ("SYNTH-S5-0001", "R02"))
        self.assertEqual([item.revision for item in self.documents.list_for_contract(self.contract.id)], ["R01", "R02"])
        self.assertEqual(first.docx_path.read_bytes(), r01_docx)
        self.assertEqual(first.pdf_path.read_bytes(), r01_pdf)
        self.assertEqual(self.event_types().count(ContractEventType.DOCUMENT_GENERATED.value), 2)
        self.assertEqual(self.context.numbering.get().next_counter, 2)


if __name__ == "__main__":
    unittest.main()
