from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from icp_renov_contracts.bootstrap import ApplicationState, build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.database import DatabaseService
from icp_renov_contracts.storage import WorkspaceService
from icp_renov_contracts.app import create_application
from icp_renov_contracts.ui.bootstrap_view import BootstrapView
from icp_renov_contracts.ui.main_window import MainWindow
from test_foundation import scratch


class BootstrapC1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.application = create_application(["bootstrap-c1"])

    def test_clean_first_use_exposes_enabled_configuration_and_restore(self):
        view = BootstrapView(lambda: None, lambda: None)
        self.assertTrue(view.configure_button.isEnabled()); self.assertFalse(view.restore_button.isHidden())

    def test_new_workspace_is_ready_only_after_database_initialization_and_config_save(self):
        with scratch() as temporary:
            root = Path(temporary); target = root / "new-workspace"; store = MachineConfigStore(root / "bootstrap.json")
            self.assertIs(build_application_context(store).state, ApplicationState.BOOTSTRAP_REQUIRED)
            workspace = WorkspaceService().ensure(target); database = DatabaseService(workspace.database_path); database.initialize()
            store.save(BootstrapConfig(workspace.root)); context = build_application_context(store)
            self.assertIs(context.state, ApplicationState.READY); self.assertEqual(store.load().active_workspace, target)
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 12)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM contracts").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM contract_documents").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM contract_events").fetchone()[0], 0)

    def test_foreign_nonempty_folder_is_identifiable_without_touching_its_sentinel(self):
        with scratch() as temporary:
            root = Path(temporary); foreign = root / "foreign"; foreign.mkdir(); sentinel = foreign / "sentinel.txt"; sentinel.write_bytes(b"C1 sentinel")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            self.assertTrue(any(foreign.iterdir())); self.assertFalse((foreign / "data" / "icp-renov.sqlite3").exists())
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_failed_fresh_attempt_removes_only_attempt_owned_workspace_paths(self):
        with scratch() as temporary:
            target = Path(temporary) / "new"; (target / "data").mkdir(parents=True); (target / "documents").mkdir(); (target / "backups").mkdir()
            (target / "data" / "icp-renov.sqlite3").write_bytes(b"attempt")
            MainWindow._cleanup_failed_workspace_attempt(target, False)
            self.assertFalse(target.exists())

    def test_failed_empty_existing_attempt_preserves_selected_root(self):
        with scratch() as temporary:
            target = Path(temporary) / "empty"; target.mkdir(); (target / "data").mkdir(); (target / "documents").mkdir(); (target / "backups").mkdir()
            MainWindow._cleanup_failed_workspace_attempt(target, True)
            self.assertTrue(target.is_dir()); self.assertFalse(any(target.iterdir()))
