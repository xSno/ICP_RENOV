from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

from icp_renov_contracts import app
from icp_renov_contracts.documents import registry
from icp_renov_contracts import runtime_resources
from test_foundation import scratch


ROOT = Path(__file__).resolve().parents[1]
WEB_UI_FILES = (
    "index.html", "app.js", "contract-workspace.js", "app.css", "clients.css",
    "contract-workspace.css", "contract-conditions.css", "contract-conditions-b2.css",
    "contract-conditions-b3.css", "contract-review.css", "contract-documents.css",
    "contract-workspace-ds01d.css",
)
UI_CONTROL_ASSETS = ("chevron-down.svg", "chevron-up.svg", "check.svg")
FORBIDDEN_WEBENGINE_FLAGS = (
    "--no-sandbox", "QTWEBENGINE_DISABLE_SANDBOX", "--disable-gpu", "remote-debugging",
)


class RuntimeResourceTests(unittest.TestCase):
    def test_runtime_resources_resolve_outside_repository_cwd(self):
        original = Path.cwd()
        with scratch() as temporary:
            os.chdir(temporary)
            try:
                self.assertTrue(runtime_resources.field_template_path().is_file())
                self.assertTrue(registry.FIELD_FILE.is_file())
                web_root = runtime_resources.web_ui_root()
                self.assertTrue((web_root / "index.html").is_file())
                for name in WEB_UI_FILES:
                    self.assertTrue((web_root / name).is_file(), name)
            finally:
                os.chdir(original)

    def test_simulated_frozen_resource_root_resolves_same_logical_paths(self):
        with scratch() as temporary:
            simulated = Path(temporary) / "icp_renov_contracts"
            shutil.copytree(runtime_resources.web_ui_root(), simulated / "ui_web")
            resource_directory = simulated / "resources"; resource_directory.mkdir()
            shutil.copy2(runtime_resources.field_template_path(), resource_directory / "FIELD_TEMPLATE_CONTRACT_V1_1.txt")
            with patch("icp_renov_contracts.runtime_resources._resource_root", return_value=simulated):
                self.assertEqual(runtime_resources.field_template_path(), resource_directory / "FIELD_TEMPLATE_CONTRACT_V1_1.txt")
                self.assertEqual(runtime_resources.web_ui_root(), simulated / "ui_web")

    def test_production_resource_resolvers_do_not_traverse_to_repository_parents(self):
        for source in (ROOT / "src" / "icp_renov_contracts" / "documents" / "registry.py",
                       ROOT / "src" / "icp_renov_contracts" / "ui" / "web_host.py"):
            self.assertNotIn("parents[", source.read_text(encoding="utf-8"), source.name)


class PackagingInputTests(unittest.TestCase):
    def test_spec_is_syntactically_valid_and_declares_only_production_assets(self):
        spec = ROOT / "packaging" / "icp_renov_contracts.spec"
        source = spec.read_text(encoding="utf-8")
        ast.parse(source)
        for name in WEB_UI_FILES:
            self.assertIn(f'"{name}"', source)
        self.assertIn("FIELD_TEMPLATE_CONTRACT_V1_1.txt", source)
        self.assertIn('UI_ASSETS = PACKAGE / "ui" / "assets"', source)
        self.assertIn('"icp_renov_contracts/ui/assets"', source)
        for name in UI_CONTROL_ASSETS:
            self.assertTrue((ROOT / "src" / "icp_renov_contracts" / "ui" / "assets" / name).is_file(), name)
        self.assertIn("collect_data_files(\"PySide6\"", source)
        self.assertIn("console=False", source)
        self.assertIn("tests", source)
        self.assertIn("spike", source)
        self.assertNotIn("soffice", source.lower())
        for forbidden in FORBIDDEN_WEBENGINE_FLAGS:
            self.assertNotIn(forbidden, source)

    def test_control_svg_assets_are_declared_as_package_data(self):
        source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"ui/assets/*.svg"', source)

    def test_build_script_uses_the_tracked_spec_without_installer_logic(self):
        source = (ROOT / "packaging" / "build_windows_bundle.ps1").read_text(encoding="utf-8")
        self.assertIn("-m PyInstaller", source)
        self.assertIn("icp_renov_contracts.spec", source)
        self.assertNotIn("Inno", source)
        self.assertNotIn("LibreOffice", source)


class StartupDiagnosticTests(unittest.TestCase):
    @patch("icp_renov_contracts.app.QMessageBox.critical")
    @patch("icp_renov_contracts.app.build_application_context", side_effect=RuntimeError("synthetic startup failure"))
    @patch("icp_renov_contracts.app.configure_logging")
    @patch("icp_renov_contracts.app.create_application")
    def test_unexpected_startup_failure_is_logged_and_shown_without_traceback(self, create, logging_setup, context, dialog):
        logger = Mock(); logging_setup.return_value = logger; create.return_value = Mock()
        self.assertEqual(app.main(["packaging-test"]), 1)
        logger.exception.assert_called_once_with("Unexpected application startup failure")
        title, message = dialog.call_args.args[1:]
        self.assertEqual(title, "ICP Renov")
        self.assertIn("n’a pas pu démarrer", message)
        self.assertNotIn("RuntimeError", message)
