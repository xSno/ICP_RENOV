from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
            self.assertEqual(set(payload), {"version", "active_workspace"})

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
            self.assertEqual(database.schema_version(), 1)
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_migration_reopen_is_idempotent(self):
        with scratch() as temporary:
            database = DatabaseService(Path(temporary) / "app.sqlite3")
            database.initialize()
            database.initialize()
            with database.connection() as connection:
                count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            self.assertEqual(count, 1)


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
