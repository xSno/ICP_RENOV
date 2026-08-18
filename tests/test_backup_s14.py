from __future__ import annotations
import shutil, unittest
from pathlib import Path
from test_foundation import scratch
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.services import BackupService, RealBackupSummaryProvider, RestoreError, RestoreService

class BackupS14Tests(unittest.TestCase):
    def setUp(self):
        self.scratch=scratch();self.root=Path(self.scratch.__enter__());self.store=MachineConfigStore(self.root/'bootstrap.json');self.store.save(BootstrapConfig(self.root/'workspace'));self.context=build_application_context(config_store=self.store);self.backup=BackupService(self.context.database,self.context.workspace,self.store)
    def tearDown(self):self.scratch.__exit__(None,None,None)
    def test_config_snapshot_archive_and_restore_new_workspace(self):
        with self.assertRaises(Exception):self.backup.set_destination(self.context.workspace.root)
        destination=self.backup.set_destination(self.root/'external');self.assertEqual(self.store.load().backup_directory,destination)
        archive=self.backup.create_now();self.assertTrue(archive.is_file());self.assertEqual(list((self.root/'external').glob('.icp-backup-*')),[]);summary=RealBackupSummaryProvider(self.backup,self.context.alerts).summary();self.assertTrue(summary.can_create_now);self.assertIn('Dernière sauvegarde',summary.label)
        restored=RestoreService(self.store).restore(archive,self.root/'restored');self.assertTrue(restored.database_path.is_file());self.assertEqual(self.store.load().active_workspace,restored.root)
    def test_rejects_nonempty_target_and_corrupt_archive(self):
        self.backup.set_destination(self.root/'external');archive=self.backup.create_now();target=self.root/'target';target.mkdir();(target/'x').write_text('x')
        with self.assertRaises(RestoreError):RestoreService(self.store).restore(archive,target)
        archive.write_bytes(b'broken')
        with self.assertRaises(RestoreError):RestoreService(self.store).restore(archive,self.root/'other')

    def test_failed_backup_cleans_its_exact_staging_directory(self):
        destination=self.backup.set_destination(self.root/'external')
        original=self.backup._snapshot
        self.backup._snapshot=lambda path: (_ for _ in ()).throw(Exception('fail'))
        with self.assertRaises(Exception): self.backup.create_now()
        self.backup._snapshot=original
        self.assertEqual(list(destination.glob('.icp-backup-*')),[])
