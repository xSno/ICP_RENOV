from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from icp_renov_contracts.app import create_application
from icp_renov_contracts.bootstrap import ApplicationState, build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.errors import (
    InvalidBootstrapConfigurationError,
    NetworkWorkspaceRejectedError,
    WorkspaceUnavailableError,
)
from icp_renov_contracts.storage import WorkspaceService
from icp_renov_contracts.ui import MainWindow, NAVIGATION_LABELS


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP = ROOT / "tests" / "tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)


@contextmanager
def scratch():
    path = TEST_TMP / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class WorkspaceTests(unittest.TestCase):
    def test_default_workspace_resolution(self):
        documents = Path("C:/Users/Test/Documents")
        self.assertEqual(
            WorkspaceService.default_workspace(documents),
            documents / "ICP Renov" / "Contrats",
        )

    def test_unc_network_paths_are_rejected(self):
        service = WorkspaceService()
        for value in (r"\\server\share\contracts", "//server/share/contracts"):
            with self.subTest(value=value):
                with self.assertRaises(NetworkWorkspaceRejectedError):
                    service.validate_candidate(Path(value))

    def test_existing_file_is_unavailable_as_workspace(self):
        with scratch() as temporary:
            path = Path(temporary) / "occupied"
            path.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(WorkspaceUnavailableError):
                WorkspaceService().ensure(path)

    def test_writable_local_workspace_initialization(self):
        with scratch() as temporary:
            workspace = WorkspaceService().ensure(Path(temporary) / "workspace")
            self.assertTrue(workspace.data_dir.is_dir())
            self.assertTrue(workspace.documents_dir.is_dir())
            self.assertTrue(workspace.backups_dir.is_dir())
            inspection = WorkspaceService().inspect(workspace.root)
            self.assertTrue(inspection.available)
            self.assertTrue(inspection.writable)


class MachineConfigTests(unittest.TestCase):
    def test_active_workspace_persistence(self):
        with scratch() as temporary:
            config_path = Path(temporary) / "machine" / "bootstrap.json"
            workspace_path = Path(temporary) / "business-workspace"
            store = MachineConfigStore(config_path)
            store.save(BootstrapConfig(workspace_path))
            self.assertEqual(store.load().active_workspace, workspace_path)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"version", "active_workspace", "backup_directory"})

    def test_invalid_machine_configuration_is_structured_error(self):
        with scratch() as temporary:
            config_path = Path(temporary) / "bootstrap.json"
            config_path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(InvalidBootstrapConfigurationError):
                MachineConfigStore(config_path).load()


class DatabaseTests(unittest.TestCase):
    def test_database_creation_and_foreign_keys(self):
        with scratch() as temporary:
            database = DatabaseService(Path(temporary) / "data" / "app.sqlite3")
            database.initialize()
            self.assertTrue(database.path.is_file())
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_migration_execution(self):
        with scratch() as temporary:
            database = DatabaseService(Path(temporary) / "app.sqlite3")
            database.initialize()
            self.assertEqual(database.schema_version(), 12)
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 12)

    def test_migration_reopen_is_idempotent(self):
        with scratch() as temporary:
            database = DatabaseService(Path(temporary) / "app.sqlite3")
            database.initialize()
            database.initialize()
            with database.connection() as connection:
                count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                self.assertEqual(count, 12)


class ApplicationContextTests(unittest.TestCase):
    def test_no_active_workspace_is_explicit_bootstrap_state(self):
        with scratch() as temporary:
            context = build_application_context(
                config_store=MachineConfigStore(Path(temporary) / "bootstrap.json")
            )
            self.assertIs(context.state, ApplicationState.BOOTSTRAP_REQUIRED)
            self.assertIsNone(context.workspace)
            self.assertIsNone(context.database)

    def test_active_workspace_bootstraps_database(self):
        with scratch() as temporary:
            root = Path(temporary)
            store = MachineConfigStore(root / "machine" / "bootstrap.json")
            store.save(BootstrapConfig(root / "workspace"))
            context = build_application_context(config_store=store)
            self.assertIs(context.state, ApplicationState.READY)
            self.assertTrue(context.workspace.database_path.is_file())


class UiShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = create_application(["test-foundation"])

    def ready_window(self, temporary: str) -> MainWindow:
        root = Path(temporary)
        store = MachineConfigStore(root / "bootstrap.json")
        store.save(BootstrapConfig(root / "workspace"))
        return MainWindow(build_application_context(config_store=store))

    def test_permanent_navigation_has_exactly_three_entries(self):
        with scratch() as temporary:
            window = self.ready_window(temporary)
            self.assertEqual(window.shell.navigation_labels, NAVIGATION_LABELS)
            self.assertEqual(
                window.shell.navigation_labels,
                ("Contrats", "Clients & installations", "Paramètres"),
            )
            window.close()

    def test_shared_light_foundation_has_fixed_palette_and_shell_width(self):
        palette = self.application.palette()
        self.assertEqual(palette.color(QPalette.ColorRole.Window).name(), "#eef3f6")
        self.assertEqual(palette.color(QPalette.ColorRole.Base).name(), "#ffffff")
        self.assertEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window).name(), "#eef3f6")
        self.assertEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base).name(), "#ffffff")
        self.assertEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name(), "#5d6c75")
        with scratch() as temporary:
            window = self.ready_window(temporary)
            self.assertEqual(window.shell.sidebar.width(), 232)
            self.assertEqual(window.shell.local_indicator.text(), "Données conservées uniquement sur ce poste.")
            self.assertEqual(window.shell.backup_indicator.text(), "Sauvegarde non configurée")
            window.close()

    def test_web_foundation_declares_light_controls_and_viewport_shell(self):
        index = (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "index.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "app.css").read_text(encoding="utf-8")
        self.assertIn('name="color-scheme" content="light"', index)
        for token in ("--app-background:#EEF3F6", "--brand:#176CA8", "--brand-hover:#115B90", "--accent:#E9A23B", "--border:#D6DFE4", "color-scheme:light"):
            self.assertIn(token, stylesheet)
        self.assertIn(".app{height:100%;min-height:0", stylesheet)
        self.assertIn(".main{min-width:0;min-height:0;overflow-y:auto", stylesheet)

    def test_web_navigation_uses_focusable_buttons(self):
        sources = [
            (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "app.js").read_text(encoding="utf-8"),
            (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8"),
        ]
        for source in sources:
            self.assertNotIn('<div class="nav', source)
            self.assertIn('<button type="button" class="nav', source)

    def test_web_contracts_and_clients_keep_shared_interaction_primitives(self):
        app_source = (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "app.js").read_text(encoding="utf-8")
        contracts = (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "app.css").read_text(encoding="utf-8")
        clients = (ROOT / "src" / "icp_renov_contracts" / "ui_web" / "clients.css").read_text(encoding="utf-8")
        self.assertIn('<tr class="contract-row"', app_source)
        self.assertIn('<button type="button" class="row-open-action"', app_source)
        self.assertIn('>Ouvrir ${icon(\'chevron\')}</button>', app_source)
        self.assertIn("event.stopPropagation();bridge.openContract('${row.id}')", app_source)
        for primitive in (".contract-row:hover", ".contract-row:focus-within", ".row-open-action:focus-visible", ".pill.SIGNED", "var(--surface)"):
            self.assertIn(primitive, contracts)
        for primitive in (".client-list-item:hover", ".client-list-item.selected", ".linked-contract-row:hover", ".drawer-overlay"):
            self.assertIn(primitive, clients)

    def test_native_settings_use_shared_ds01_navigation_and_content_primitives(self):
        root = ROOT / "src" / "icp_renov_contracts" / "ui"
        styles = (root / "styles.py").read_text(encoding="utf-8")
        settings = (root / "settings_view.py").read_text(encoding="utf-8")
        models = (root / "models_settings_view.py").read_text(encoding="utf-8")
        numbering = (root / "numbering_alerts_view.py").read_text(encoding="utf-8")
        diagnostic = (root / "generation_diagnostic_view.py").read_text(encoding="utf-8")
        for primitive in ("QFrame#settingsNavigationPanel", "QStackedWidget#settingsContentStack", "QTableWidget#modelsTable", "QPushButton#sensitiveButton", "QLabel#infoFeedback", 'QLabel#diagnosticFeedback[tone="success"]', "QFrame#diagnosticCapabilityRow"):
            self.assertIn(primitive, styles)
        self.assertIn('setObjectName("settingsNavigationPanel")', settings)
        self.assertIn('navigation_panel.setMinimumWidth(236)', settings)
        self.assertIn('root.addWidget(navigation_panel, 0, Qt.AlignmentFlag.AlignTop)', settings)
        self.assertIn('setObjectName("settingsContentStack")', settings)
        self.assertIn('root.setAlignment(Qt.AlignmentFlag.AlignTop)', models)
        self.assertIn('root.addStretch(1); return page', models)
        self.assertIn('setFixedHeight(min(360', models)
        self.assertIn('class _ModelsRowHoverDelegate', models)
        self.assertIn('class _ModelsTableWidget', models)
        self.assertIn('self.setMouseTracking(True)', models)
        self.assertNotIn('QTableWidget#modelsTable::item:hover', styles)
        for forbidden in ('selectRow(', 'setCurrentCell(', 'setCurrentItem('):
            self.assertNotIn(forbidden, models)
        self.assertIn('setObjectName("sensitiveButton")', numbering)
        self.assertIn("setObjectName('diagnosticModelSelector')", diagnostic)
        for primitive in ("def _set_feedback", "self.feedback.setVisible(bool(text))", "style.unpolish(self.feedback)", "'warning'", "'error'", "'success'", "def _capability_row"):
            self.assertIn(primitive, diagnostic)

    def test_native_settings_controls_and_shell_use_ds01_parity_primitives(self):
        root = ROOT / "src" / "icp_renov_contracts" / "ui"
        styles = (root / "styles.py").read_text(encoding="utf-8")
        shell = (root / "shell.py").read_text(encoding="utf-8")
        settings = (root / "settings_view.py").read_text(encoding="utf-8")
        models = (root / "models_settings_view.py").read_text(encoding="utf-8")
        diagnostic = (root / "generation_diagnostic_view.py").read_text(encoding="utf-8")
        for primitive in ("QComboBox::drop-down", "QComboBox QAbstractItemView", "QComboBox::down-arrow", "QAbstractSpinBox::up-button", "QAbstractSpinBox::up-arrow", "QCheckBox::indicator", "QCheckBox::indicator:checked", "QScrollBar:vertical", "QScrollBar::add-line", "image: url", "_control_asset_url"):
            self.assertIn(primitive, styles)
        self.assertNotIn("border: solid", styles)
        for primitive in ("_SIDEBAR_ICON_PATHS", '"air"', "def _sidebar_icon", "button.setIcon(_sidebar_icon", "def _sidebar_status_block", '"Mode local"', '"Données conservées uniquement sur ce poste."', '"Sauvegarde"', "sidebar.setFixedWidth(232)"):
            self.assertIn(primitive, shell)
        for primitive in ('QWidget#contentSurface QPushButton#primaryButton:disabled', 'QWidget#contentSurface QPushButton#tertiaryButton', 'QWidget#contentSurface QPushButton#sensitiveButton', 'QPushButton[busy="true"]'):
            self.assertIn(primitive, styles)
        self.assertIn('open_workspace.setObjectName("tertiaryButton")', settings)
        self.assertIn('self.restore_backup.setObjectName("secondaryButton")', settings)
        self.assertIn("((2, 75), (3, 120), (5, 110))", models)
        self.assertIn('back.setObjectName("tertiaryButton")', models)
        self.assertIn('open_source.setObjectName("secondaryButton")', models)
        self.assertIn("self.test.setObjectName('primaryButton')", diagnostic)

    def test_navigation_ampersands_are_escaped_for_qt_rendering(self):
        with scratch() as temporary:
            window = self.ready_window(temporary)
            self.assertEqual(
                window.shell._buttons["Clients & installations"].text(),
                "Clients && installations",
            )
            window.close()

    def test_contracts_is_default_surface(self):
        with scratch() as temporary:
            window = self.ready_window(temporary)
            self.assertEqual(window.shell.current_surface, "Contrats")
            window.close()

    def test_navigation_switches_surfaces_deterministically(self):
        with scratch() as temporary:
            window = self.ready_window(temporary)
            for destination in ("Clients & installations", "Paramètres", "Contrats"):
                window.shell.navigate(destination)
                self.assertEqual(window.shell.current_surface, destination)
            window.close()

    def test_external_landings_handle_normal_navigation_but_not_workflows(self):
        with scratch() as temporary:
            window = self.ready_window(temporary)
            shell = window.shell
            landings: list[str] = []
            shell.set_external_landing("Contrats", lambda: landings.append("CONTRACTS"))
            shell.set_external_landing("Clients & installations", lambda: landings.append("CLIENTS"))
            shell.navigate("Contrats")
            shell.navigate("Clients & installations")
            self.assertEqual(landings, ["CONTRACTS", "CLIENTS"])
            shell.navigate("Contrats", workflow=True)
            self.assertEqual(shell.current_surface, "Contrats")
            shell.navigate("Clients & installations", workflow=True)
            self.assertEqual(shell.current_surface, "Clients & installations")
            self.assertEqual(landings, ["CONTRACTS", "CLIENTS"])
            shell.navigate("Paramètres")
            self.assertEqual(shell.current_surface, "Paramètres")
            self.assertEqual(shell.navigation_labels, NAVIGATION_LABELS)
            window.close()

    def test_bootstrap_view_replaces_permanent_navigation(self):
        with scratch() as temporary:
            context = build_application_context(
                config_store=MachineConfigStore(Path(temporary) / "bootstrap.json")
            )
            window = MainWindow(context)
            self.assertIsNone(window.shell)
            self.assertEqual(window.bootstrap_view.configure_button.text(), "Configurer ce poste")
            self.assertEqual(
                window.bootstrap_view.restore_button.text(),
                "Restaurer une sauvegarde existante",
            )
            window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
