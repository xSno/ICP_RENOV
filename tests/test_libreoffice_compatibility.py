from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QLabel

from icp_renov_contracts.documents import LibreOfficeConverter, NonOfficialModelValidationRunner, ProductionDocxRenderer, StaticCompanyDocumentDataProvider
from icp_renov_contracts.documents.libreoffice import LibreOfficeRuntimeProbe, LibreOfficeState, QUALIFIED_LIBREOFFICE_VERSIONS, discover_libreoffice_executable
from icp_renov_contracts.documents.source_store import sha256_file
from icp_renov_contracts.documents.validation import DocumentGenerationError
from icp_renov_contracts.services.capabilities import DocumentCapabilityProbe
from icp_renov_contracts.services.generation_diagnostic import GenerationDiagnosticService
from icp_renov_contracts.ui.generation_diagnostic_view import GenerationDiagnosticPage

from test_document_generation import COMPANY, GenerationCase
from test_foundation import scratch
from test_intervention_s9 import InterventionCase


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.temporary = scratch()
        self.root = Path(self.temporary.__enter__())
        self.exe = self.root / "soffice.com"
        self.exe.write_bytes(b"synthetic executable")
        self.runtime = LibreOfficeRuntimeProbe(self.exe)
        self.converter = LibreOfficeConverter(runtime=self.runtime)
        self.probe = DocumentCapabilityProbe(runtime=self.runtime)
        self.run_patch = patch("icp_renov_contracts.documents.libreoffice.subprocess.run")
        self.run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def tearDown(self):
        self.temporary.__exit__(None, None, None)

    def version(self, version="26.8.0.3", code=0):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], code, f"LibreOffice {version} build-id\n", "")
        return self.runtime.refresh()

    @staticmethod
    def process(command, **kwargs):
        # Mock the process only: exercise the real headless conversion/cleanup code.
        output = Path(command[command.index("--outdir") + 1]) / (Path(command[-1]).stem + ".pdf")
        output.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")
        return SimpleNamespace(returncode=0, communicate=lambda **_: ("", ""))


class LibreOfficeRuntimeTests(RuntimeCase):
    def test_qualified_set_and_exact_versions(self):
        self.assertEqual(QUALIFIED_LIBREOFFICE_VERSIONS, frozenset({"26.2.5.2"}))
        self.assertNotIn("26.8.0.3", QUALIFIED_LIBREOFFICE_VERSIONS)
        for version, state in (("26.2.5.2", LibreOfficeState.QUALIFIED),
                               ("26.8.0.3", LibreOfficeState.UNQUALIFIED),
                               ("26.2.6.0", LibreOfficeState.UNQUALIFIED)):
            with self.subTest(version=version):
                status = self.version(version)
                self.assertIs(status.state, state)
                self.assertEqual(status.version, version)
                self.assertEqual(status.executable, self.exe.resolve())
                self.assertEqual(status.version_output, f"LibreOffice {version} build-id\n")
                self.assertEqual(self.converter.available(), state is LibreOfficeState.QUALIFIED)
                with self.assertRaises(FrozenInstanceError): status.version = "other"

    def test_absent_and_resolved_but_failed_probe_are_distinct(self):
        with patch("icp_renov_contracts.documents.libreoffice.discover_libreoffice_executable", return_value=None):
            self.assertIs(LibreOfficeRuntimeProbe().inspect().state, LibreOfficeState.ABSENT)
        self.run.assert_not_called()
        for failure in (OSError("unreadable"), subprocess.TimeoutExpired("soffice", 15)):
            with self.subTest(failure=failure):
                self.run.side_effect = failure
                status = self.runtime.refresh()
                self.assertIs(status.state, LibreOfficeState.PROBE_FAILED)
                self.assertEqual(status.executable, self.exe.resolve())
                self.assertIsNone(status.version)

    def test_nonzero_unparseable_and_ambiguous_output_fail_closed(self):
        for code, output in ((1, "LibreOffice 26.2.5.2"), (0, "26.2.5.2"),
                             (0, "LibreOffice 26.2.5.2.1"), (0, "LibreOffice 26.2.5.2junk"),
                             (0, "LibreOffice 26.2.5.2\nLibreOffice 26.8.0.3")):
            with self.subTest(code=code, output=output):
                self.run.return_value = subprocess.CompletedProcess([], code, output, "")
                status = self.runtime.refresh()
                self.assertIs(status.state, LibreOfficeState.PROBE_FAILED)
                self.assertEqual(status.version_output, output)

    def test_shared_cache_refresh_expiry_and_windowless_probe(self):
        status = self.version("26.2.5.2")
        self.assertTrue(self.converter.available())
        self.assertTrue(self.probe.probe().pdf_available)
        self.assertIs(self.runtime.inspect(), status)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.run.call_args.args[0], [str(self.exe.resolve()), "--version"])
        self.assertEqual(self.run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.probe.probe(refresh=True)
        self.assertEqual(self.run.call_count, 2)
        with patch("icp_renov_contracts.documents.libreoffice.time.monotonic", return_value=self.runtime._checked_at + 6):
            self.converter.available()
        self.assertEqual(self.run.call_count, 3)

    def test_discovery_preserves_all_path_and_program_files_candidates(self):
        for name in ("soffice.com", "soffice.exe", "soffice"):
            with self.subTest(name=name), patch("icp_renov_contracts.documents.libreoffice.shutil.which",
                    side_effect=lambda candidate: str(self.exe) if candidate == name else None):
                self.assertEqual(discover_libreoffice_executable(), self.exe.resolve())
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            for name in ("soffice.com", "soffice.exe"):
                with self.subTest(variable=variable, name=name):
                    installed = self.root / variable / name / "LibreOffice" / "program" / name
                    installed.parent.mkdir(parents=True)
                    installed.write_bytes(b"fixture")
                    with patch("icp_renov_contracts.documents.libreoffice.shutil.which", return_value=None), patch.dict(
                            "icp_renov_contracts.documents.libreoffice.os.environ", {variable: str(installed.parents[2])}, clear=True):
                        self.assertEqual(discover_libreoffice_executable(), installed.resolve())

    def test_official_unqualified_refusal_and_candidate_real_internal_path(self):
        status = self.version()
        docx = self.root / "input.docx"; docx.write_bytes(b"synthetic")
        pdf = self.root / "result.pdf"
        with patch("icp_renov_contracts.documents.converter.subprocess.Popen", side_effect=self.process) as process:
            with self.assertRaises(DocumentGenerationError): self.converter.convert(docx, pdf)
            process.assert_not_called()
            self.converter.convert_candidate(docx, pdf)
            self.assertTrue(pdf.is_file())
            command = process.call_args.args[0]
            self.assertEqual(command[0], str(status.executable))
            self.assertIn("--headless", command)
            profile = next(item for item in command if item.startswith("-env:UserInstallation="))
            self.assertIn("file:", profile)
            self.assertEqual(process.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertFalse(list(self.root.glob("lo_output_*")))
        self.assertIs(self.runtime.inspect(), status)
        self.assertFalse(self.converter.available())
        self.assertFalse(self.probe.probe().pdf_available)
        self.assertEqual(QUALIFIED_LIBREOFFICE_VERSIONS, frozenset({"26.2.5.2"}))

    def test_qualified_production_and_candidate_use_identical_internal_path(self):
        self.version("26.2.5.2")
        docx = self.root / "input.docx"; docx.write_bytes(b"fixture")
        with patch("icp_renov_contracts.documents.converter.subprocess.Popen", side_effect=self.process) as process:
            self.converter.convert(docx, self.root / "official.pdf")
            self.converter.convert_candidate(docx, self.root / "candidate.pdf")
            self.assertEqual(process.call_count, 2)
            self.assertTrue(all(call.args[0][0] == str(self.exe.resolve()) for call in process.call_args_list))

    def test_candidate_blocks_absent_and_probe_failed(self):
        for state in (LibreOfficeState.ABSENT, LibreOfficeState.PROBE_FAILED):
            with self.subTest(state=state):
                if state is LibreOfficeState.ABSENT: self.exe.unlink()
                else:
                    self.exe.write_bytes(b"fixture"); self.run.side_effect = OSError("probe")
                self.assertIs(self.runtime.refresh().state, state)
                with patch("icp_renov_contracts.documents.converter.subprocess.Popen") as process:
                    with self.assertRaises(DocumentGenerationError):
                        self.converter.convert_candidate(self.root / "in.docx", self.root / "out.pdf")
                    process.assert_not_called()

    def test_shared_conversion_timeout_kills_tree_and_cleans_attempt(self):
        self.version()
        process = Mock(pid=123, returncode=1)
        process.communicate.side_effect = [subprocess.TimeoutExpired("soffice", 180), ("", "")]
        with patch("icp_renov_contracts.documents.converter.subprocess.Popen", return_value=process) as popen:
            with self.assertRaises(DocumentGenerationError) as error:
                self.converter.convert_candidate(self.root / "input.docx", self.root / "out.pdf")
        self.assertEqual(error.exception.code, "converter_timeout")
        self.assertEqual(process.communicate.call_args_list[0].kwargs["timeout"], 180)
        command = popen.call_args.args[0]
        if os.name == "nt":
            self.assertEqual(self.run.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])
            self.assertEqual(self.run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            process.kill.assert_called_once()
        self.assertFalse(Path(command[command.index("--outdir") + 1]).exists())
        self.assertFalse((self.root / "out.pdf").exists())


class LibreOfficeDiagnosticTests(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.version()
        self.version_data = SimpleNamespace(id="v1", document_kind="CONTRACT", required_company_fields=(),
                                           source_relpath="templates/fixture.docx", source_hash="synthetic-hash",
                                           allowed_client_regimes=("CONSUMER",), target_client_regimes=("CONSUMER",))
        self.source = next((Path(__file__).parents[1] / "templates").glob("*HABITATION*.docx"))
        self.catalog = Mock()
        self.catalog.source_integrity.return_value = ("VALID", "Source conforme")
        self.catalog.company_provider = StaticCompanyDocumentDataProvider(COMPANY)
        self.catalog.get_version.return_value = self.version_data
        self.catalog.source_store.verify.return_value = self.source
        self.catalog.list_all.return_value = []
        self.runner = NonOfficialModelValidationRunner(self.root, ProductionDocxRenderer(), self.converter, self.catalog.company_provider)
        self.workspace_service = Mock()
        self.workspace_service.inspect.return_value = SimpleNamespace(available=True, writable=True)
        self.service = GenerationDiagnosticService(SimpleNamespace(root=self.root), self.workspace_service,
                                                   self.catalog, self.runner, self.probe)

    def test_candidate_success_runs_real_docx_pdf_validation_and_returns_evidence(self):
        with patch("icp_renov_contracts.documents.converter.subprocess.Popen", side_effect=self.process):
            result = self.service.run("v1")
        self.assertTrue(result.succeeded, result.issue)
        self.assertTrue(result.compatibility_candidate)
        self.assertIs(result.libreoffice_state, LibreOfficeState.UNQUALIFIED)
        self.assertEqual(result.libreoffice_version, "26.8.0.3")
        self.assertEqual(result.executable, self.exe.resolve())
        self.assertEqual(result.version_output, "LibreOffice 26.8.0.3 build-id\n")
        self.assertEqual(result.tested_cases, ("equipment-1", "equipment-10", "equipment-30"))
        self.assertEqual((len(result.docx_paths), len(result.pdf_paths)), (3, 3))
        self.assertTrue(all(path.is_file() and sha256_file(path) for path in result.docx_paths + result.pdf_paths))
        self.catalog.test_generation.assert_not_called()
        self.assertFalse(self.converter.available())
        self.assertFalse(self.service.workstation_status().pdf_available)
        self.assertIs(self.runtime.inspect().state, LibreOfficeState.UNQUALIFIED)
        self.assertEqual(QUALIFIED_LIBREOFFICE_VERSIONS, frozenset({"26.2.5.2"}))

    def test_candidate_failure_does_not_promote_or_persist(self):
        with patch.object(self.converter, "convert_candidate", side_effect=DocumentGenerationError("converter_failure", "PDF de test impossible")):
            result = self.service.run("v1")
        self.assertFalse(result.succeeded)
        self.assertIn("test local de compatibilité a échoué", result.issue)
        self.assertTrue(result.compatibility_candidate)
        self.assertFalse(self.probe.probe().pdf_available)
        self.catalog.test_generation.assert_not_called()

    def test_unqualified_governance_and_default_runner_stay_strict(self):
        with patch.object(self.converter, "convert_candidate") as candidate:
            result = self.service.run("v1", validation_mode=True)
            self.assertFalse(result.succeeded)
            self.assertFalse(result.compatibility_candidate)
            self.catalog.test_generation.assert_not_called()
            with self.assertRaises(DocumentGenerationError): self.runner.run(self.version_data, self.source)
            candidate.assert_not_called()
        self.assertFalse((self.root / "validation").exists())

    def test_prerequisite_failure_does_not_claim_candidate_conversion_was_used(self):
        self.catalog.source_integrity.return_value = ("MISSING", "Fichier source introuvable")
        with patch.object(self.runner, "run") as run:
            result = self.service.run("v1")
        self.assertFalse(result.succeeded)
        self.assertFalse(result.compatibility_candidate)
        self.assertIs(result.libreoffice_state, LibreOfficeState.UNQUALIFIED)
        run.assert_not_called()

    def test_qualified_diagnostic_never_uses_candidate_route(self):
        self.version("26.2.5.2")
        with patch.object(self.converter, "convert_candidate") as candidate, patch(
                "icp_renov_contracts.documents.converter.subprocess.Popen", side_effect=self.process):
            result = self.service.run("v1")
        self.assertTrue(result.succeeded)
        self.assertFalse(result.compatibility_candidate)
        candidate.assert_not_called()

    def test_absent_and_probe_failed_diagnostic_are_bounded_without_rendering(self):
        for state, text in ((LibreOfficeState.ABSENT, "LibreOffice absent"),
                            (LibreOfficeState.PROBE_FAILED, "version impossible à vérifier")):
            if state is LibreOfficeState.ABSENT: self.exe.unlink()
            else:
                self.exe.write_bytes(b"fixture"); self.run.side_effect = OSError("failed")
            with self.subTest(state=state), patch.object(self.runner, "run") as run:
                result = self.service.run("v1")
                self.assertFalse(result.succeeded)
                self.assertIn(text, result.issue)
                self.assertIs(result.libreoffice_state, state)
                run.assert_not_called()

    def test_diagnostic_ui_distinguishes_all_states_and_candidate_feedback(self):
        app = QApplication.instance() or QApplication(["compatibility"])
        page = GenerationDiagnosticPage(self.service, self.catalog)
        self.addCleanup(page.close)
        for version, expected in (("26.2.5.2", "pris en charge"), ("26.8.0.3", "version non qualifiée")):
            self.version(version); page.refresh(); app.processEvents()
            self.assertTrue(any(expected in label.text() for label in page.workstation.findChildren(QLabel)))
        page.version.addItem("Version de test", "v1")
        self.assertTrue(page.test.isEnabled())
        with patch("icp_renov_contracts.documents.converter.subprocess.Popen", side_effect=self.process):
            page.result = self.service.run("v1")
        page._show_result()
        self.assertIn("Test local réussi — version non encore qualifiée", page.feedback.text())
        self.assertNotIn("PDF disponible", page.feedback.text())
        self.assertFalse(self.probe.probe().pdf_available)
        with patch.object(self.converter, "convert_candidate", side_effect=DocumentGenerationError("converter_failure", "PDF de test impossible")):
            page.result = self.service.run("v1")
        page._show_result()
        self.assertEqual(page.feedback.property('tone'), 'error')
        self.assertIn('test local de compatibilité a échoué', page.feedback.text())
        self.assertFalse(self.probe.probe().pdf_available)
        self.exe.unlink(); page.refresh(); app.processEvents()
        self.assertTrue(any("LibreOffice absent" in label.text() for label in page.workstation.findChildren(QLabel)))
        self.exe.write_bytes(b"fixture"); self.run.side_effect = OSError("probe"); page.refresh(); app.processEvents()
        self.assertTrue(any("version impossible à vérifier" in label.text() for label in page.workstation.findChildren(QLabel)))


class LibreOfficeProductionGateTests(GenerationCase):
    def test_bootstrap_shares_exact_runtime_instance(self):
        converter = self.context.generation.converter
        runtime = converter.runtime
        self.assertIs(self.context.intervention_generation.converter, converter)
        self.assertIs(self.context.template_catalog.validation_runner.converter, converter)
        self.assertIs(self.context.review.capability_probe.runtime, runtime)
        self.assertIs(self.context.diagnostic.capability_probe.runtime, runtime)
        self.assertIs(self.context.review.capability_probe, self.context.diagnostic.capability_probe)

    def test_unqualified_docx_is_intrinsic_but_official_combined_generation_stays_blocked(self):
        exe = self.temporary / "soffice.com"; exe.write_bytes(b"fixture")
        runtime = LibreOfficeRuntimeProbe(exe)
        converter = LibreOfficeConverter(runtime=runtime)
        service = self.service(converter=converter)
        self.review.generation_ready = None
        self.review.generation_readiness = service.readiness
        self.review.capability_probe = DocumentCapabilityProbe(runtime=runtime)
        with patch("icp_renov_contracts.documents.libreoffice.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "LibreOffice 26.8.0.3", "")):
            result = self.review.review(self.contract.id)
            self.assertTrue(next(check for check in result.generation.checks if check.key == "DOCX").available)
            self.assertFalse(next(check for check in result.generation.checks if check.key == "PDF").available)
            self.assertFalse(result.generation_available)
            self.assertFalse(service.available(self.contract.id))
            self.assert_atomic_failure(service)


class LibreOfficeInterventionGateTests(InterventionCase):
    def test_unqualified_intervention_official_gate_stays_strict(self):
        exe = self.temporary / "soffice.com"; exe.write_bytes(b"fixture")
        self.sheet_service.converter = LibreOfficeConverter(exe)
        before = self.documents.list_for_contract(self.contract.id)
        with patch("icp_renov_contracts.documents.libreoffice.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "LibreOffice 26.8.0.3", "")):
            with self.assertRaises(DocumentGenerationError): self.generate_sheet()
        self.assertEqual(self.documents.list_for_contract(self.contract.id), before)
