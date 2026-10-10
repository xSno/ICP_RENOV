from __future__ import annotations

import ctypes
import errno
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from test_foundation import scratch
from icp_renov_contracts.bootstrap import build_application_context
from icp_renov_contracts.config import BootstrapConfig, MachineConfigStore
from icp_renov_contracts.errors import InvalidBootstrapConfigurationError
from icp_renov_contracts.services import BackupService, RestoreError, RestoreService
from icp_renov_contracts.services import backup as module
from icp_renov_contracts.ui.main_window import MainWindow
from icp_renov_contracts.ui.settings_view import CompanySettingsView


class RestoreCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.scratch = scratch();self.root = Path(self.scratch.__enter__())
        self.addCleanup(self.scratch.__exit__, None, None, None)
        self.store = MachineConfigStore(self.root/'bootstrap.json')
        self.store.save(BootstrapConfig(self.root/'workspace'))
        self.context = build_application_context(self.store)
        self.context.company.save(replace(self.context.company.get(), city='Saint-Raphaël', signatory_role='Gérant'))
        self.document = self.context.workspace.documents_dir/'synthetic.txt'
        self.document.write_bytes(b'synthetic document A')
        self.context.backup.set_destination(self.root/'archives')
        self.archive = self.context.backup.create_now()
        self.source_bytes = self.archive.read_bytes()
        self.previous = self.store.load()
        self.target = self.root/'restored'
        self.service = RestoreService(self.store)
        self.stages = []
        original = self.service._source_stage
        def stage(*args):
            result = original(*args);self.stages.append(result)
            return result
        self.service._source_stage = stage
        self.addCleanup(self.cleanup_stages)

    def cleanup_stages(self):
        for p in self.stages:
            if p.exists(): shutil.rmtree(p)

    def preserved(self, source=True, target_absent=True):
        self.assertEqual(self.store.load(), self.previous)
        if source: self.assertEqual(self.archive.read_bytes(), self.source_bytes)
        if target_absent: self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob('restored.restore-*')))
        self.assertTrue(all(not p.exists() for p in self.stages))

    def test_backup_restore_fidelity_activation_and_readonly_source(self):
        before_hash = hashlib.sha256(self.source_bytes).hexdigest()
        if os.name == 'nt': self.archive.chmod(0o444)
        self.addCleanup(self.archive.chmod, 0o666)
        restored = self.service.restore(self.archive, self.target)
        self.assertEqual(self.store.load(), BootstrapConfig(restored.root, self.previous.backup_directory))
        self.assertEqual((restored.documents_dir/'synthetic.txt').read_bytes(), self.document.read_bytes())
        fresh = build_application_context(self.store)
        self.assertEqual(fresh.company.get(), self.context.company.get())
        with fresh.database.connection() as db: self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), before_hash)
        self.assertFalse(list(self.root.glob('restored.restore-*')))
        self.assertTrue(all(not p.exists() for p in self.stages))

    def test_unicode_spaces_drive_letter_and_existing_empty_destination(self):
        directory = self.root/'Copie été avec espaces';directory.mkdir()
        archive = directory/'sauvegarde été.icprenovbackup';shutil.copyfile(self.archive, archive)
        self.target.mkdir()
        restored = self.service.restore(Path(archive.as_posix()), self.target, activate=False)
        self.assertEqual(restored.root, self.target.resolve())
        if os.name == 'nt': self.assertTrue(archive.drive)
        self.assertEqual(archive.read_bytes(), self.source_bytes)
        self.preserved(target_absent=False)

    def test_missing_source_maps_and_cleans(self):
        with self.assertRaises(RestoreError) as raised: self.service.restore(self.root/'missing.icprenovbackup', self.target)
        self.assertEqual(raised.exception.code, 'source_copy');self.assertIsInstance(raised.exception.__cause__, FileNotFoundError)
        self.preserved()

    def test_access_denied_and_sharing_violation_map(self):
        for failure in (PermissionError(13, 'access denied'), ctypes.WinError(32) if os.name == 'nt' else PermissionError(13, 'sharing')):
            with self.subTest(failure=failure), patch.object(self.service, '_copy_source', side_effect=failure):
                with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
                self.assertIs(raised.exception.__cause__, failure)
                self.preserved()

    @unittest.skipUnless(os.name == 'nt', 'Windows native exclusive lock')
    def test_real_windows_exclusive_source_lock(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.archive), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
            self.assertIsInstance(raised.exception.__cause__, PermissionError)
        finally: kernel.CloseHandle(handle)
        self.preserved()

    def test_copy_partial_unplug_disk_full_and_truncation(self):
        for mode in ('unplug', 'disk_full', 'partial_return', 'truncated'):
            def copy(source, staged):
                staged.write(self.source_bytes[:40]);staged.seek(0)
                if mode == 'unplug': raise ctypes.WinError(1167) if os.name == 'nt' else OSError('unplug')
                if mode == 'disk_full': raise OSError(28, 'disk full')
                if mode == 'truncated': raise RestoreError('source_truncated')
            with self.subTest(mode=mode), patch.object(self.service, '_copy_source', side_effect=copy):
                with self.assertRaises(RestoreError): self.service.restore(self.archive, self.target)
                self.preserved()

    def test_streaming_short_read_is_rejected(self):
        source = Mock();source.__enter__ = Mock(return_value=source);source.__exit__ = Mock(return_value=False)
        source.fileno.return_value = 123;source.read.side_effect = [b'partial', b'']
        staged = Mock()
        with patch.object(Path, 'open', return_value=source), patch.object(module.os, 'fstat', return_value=SimpleNamespace(st_size=100)):
            with self.assertRaises(RestoreError) as raised: RestoreService._copy_source(self.archive, staged)
        self.assertEqual(raised.exception.code, 'source_truncated')
        self.assertEqual(source.read.call_args_list[0].args, (1024*1024,))

    def test_fixed_volume_required_and_staging_permission_failure(self):
        with patch.object(self.service, '_fixed_local', return_value=False):
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
            self.assertEqual(raised.exception.code, 'local_staging')
        with patch.object(module.tempfile, 'mkdtemp', side_effect=PermissionError(13, 'staging denied')):
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
            self.assertEqual(raised.exception.code, 'local_staging')
        self.preserved()

    def test_staging_outside_source_active_and_destination(self):
        original = BackupService._validate_zip
        def validate(z, temporary_directory=None):
            p = Path(temporary_directory).resolve()
            for excluded in (self.archive.parent.resolve(), self.target.resolve(), self.context.workspace.root.resolve()):
                self.assertNotEqual(p, excluded);self.assertNotIn(excluded, p.parents)
            self.assertTrue(self.service._fixed_local(p))
            return original(z, temporary_directory)
        with patch.object(BackupService, '_validate_zip', side_effect=validate):
            self.service.restore(self.archive, self.target, activate=False)
        self.preserved(target_absent=False)

    def test_source_substitution_after_copy_uses_same_validated_zip(self):
        self.document.write_bytes(b'synthetic document B')
        other = self.context.backup.create_now()
        original = BackupService._validate_zip
        zip_objects = []
        def validate(z, temporary_directory=None):
            zip_objects.append(z)
            self.archive.write_bytes(other.read_bytes())
            return original(z, temporary_directory)
        original_read = zipfile.ZipFile.read
        observed = []
        def read(z, *args, **kwargs): observed.append(z);return original_read(z, *args, **kwargs)
        with patch.object(BackupService, '_validate_zip', side_effect=validate), patch.object(zipfile.ZipFile, 'read', read):
            restored = self.service.restore(self.archive, self.target, activate=False)
        self.assertEqual((restored.documents_dir/'synthetic.txt').read_bytes(), b'synthetic document A')
        self.assertEqual(len(zip_objects), 1);self.assertTrue(all(z is zip_objects[0] for z in observed))
        self.assertEqual(self.store.load(), self.previous)

    def malformed(self, mode):
        path = self.root/(mode+'.icprenovbackup')
        with zipfile.ZipFile(self.archive) as z: contents = {n:z.read(n) for n in z.namelist()}
        manifest = json.loads(contents['manifest.json'])
        if mode == 'invalid_zip': path.write_bytes(b'bad');return path
        if mode == 'missing_manifest': del contents['manifest.json']
        elif mode == 'invalid_manifest': contents['manifest.json'] = b'not-json'
        elif mode == 'hash': contents['documents/synthetic.txt'] = b'changed'
        elif mode == 'database':
            name = manifest['database_path'];contents[name] = b'not-sqlite'
            for e in manifest['files']:
                if e['path'] == name: e.update(size=len(contents[name]), sha256=hashlib.sha256(contents[name]).hexdigest())
            contents['manifest.json'] = json.dumps(manifest).encode()
        elif mode == 'traversal':
            name = 'documents/synthetic.txt';contents['../outside.txt'] = contents.pop(name)
            for e in manifest['files']:
                if e['path'] == name: e['path'] = '../outside.txt'
            contents['manifest.json'] = json.dumps(manifest).encode()
        elif mode == 'unexpected': contents['unexpected.txt'] = b'foreign'
        elif mode == 'future':
            manifest['source_schema_version'] = 999;contents['manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, 'w') as z:
            for n, data in contents.items(): z.writestr(n, data)
            if mode == 'duplicate':
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning);z.writestr('documents/synthetic.txt', contents['documents/synthetic.txt'])
        return path

    def test_archive_integrity_rejections_and_future_schema(self):
        for mode in ('invalid_zip','missing_manifest','invalid_manifest','hash','database','traversal','unexpected','duplicate','future'):
            with self.subTest(mode=mode):
                path = self.malformed(mode)
                with self.assertRaises(RestoreError) as raised: self.service.restore(path, self.target)
                if mode == 'future':
                    self.assertEqual(raised.exception.code, 'future_schema');self.assertIn('version plus récente', raised.exception.user_message)
                self.preserved()

    def test_crc_and_unsupported_zip_read_are_normalized(self):
        for failure in (zipfile.BadZipFile('Bad CRC-32'), RuntimeError('encrypted ZIP'), NotImplementedError('compression')):
            with self.subTest(failure=failure), patch.object(zipfile.ZipFile, 'read', side_effect=failure):
                with self.assertRaises(RestoreError): self.service.restore(self.archive, self.target)
                self.preserved()

    def test_foreign_target_untouched(self):
        self.target.mkdir();sentinel = self.target/'sentinel.txt';sentinel.write_bytes(b'foreign')
        with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
        self.assertEqual(raised.exception.code, 'nonempty');self.assertEqual(sentinel.read_bytes(), b'foreign')
        self.assertEqual(list(self.target.iterdir()), [sentinel]);self.preserved(target_absent=False)

    def test_destination_stage_collision_never_cleans_foreign_directory(self):
        collision = self.root/'restored.restore-collision'
        collision.mkdir();sentinel = collision/'foreign-sentinel.txt'
        sentinel.write_bytes(b'foreign content must survive')
        before = sentinel.read_bytes()
        with patch.object(module.uuid, 'uuid4', return_value=SimpleNamespace(hex='collision')), patch.object(module.shutil, 'rmtree', wraps=shutil.rmtree) as cleanup:
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
            self.assertEqual(raised.exception.code, 'extraction')
            self.assertIsInstance(raised.exception.__cause__, FileExistsError)
            self.assertTrue(all(Path(call.args[0]) != collision for call in cleanup.call_args_list))
        self.assertTrue(collision.is_dir());self.assertEqual(sentinel.read_bytes(), before)
        self.assertEqual(list(collision.iterdir()), [sentinel])
        self.assertEqual(self.archive.read_bytes(), self.source_bytes)
        self.assertEqual(self.store.load(), self.previous);self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('restored.restore-*')), [collision])
        self.assertTrue(all(not p.exists() for p in self.stages))

    def test_truncated_zip_eof_is_bounded_in_validation_and_extraction(self):
        original = zipfile.ZipFile.read
        for phase in ('validation', 'extraction'):
            counts = {};failure = EOFError('truncated compressed entry')
            def read(z, name, *args, **kwargs):
                counts[name] = counts.get(name, 0)+1
                if name == 'documents/synthetic.txt' and counts[name] == (1 if phase == 'validation' else 2): raise failure
                return original(z, name, *args, **kwargs)
            with self.subTest(phase=phase), patch.object(zipfile.ZipFile, 'read', read):
                with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
                self.assertIs(raised.exception.__cause__, failure)
                self.assertIn('illisible ou incomplète', raised.exception.user_message)
                self.preserved()
        with patch.object(zipfile.ZipFile, 'read', side_effect=EOFError('truncated')):
            with self.assertRaises(RestoreError): BackupService.validate_archive(self.archive)

    def test_disk_full_has_specific_local_storage_message_and_diagnostics(self):
        failures = [OSError(errno.ENOSPC, 'no space')]
        if os.name == 'nt': failures.append(ctypes.WinError(112))
        for failure in failures:
            for phase in ('staging', 'copy', 'extraction'):
                if phase == 'staging': injection = patch.object(module.tempfile, 'mkdtemp', side_effect=failure)
                elif phase == 'copy': injection = patch.object(self.service, '_copy_source', side_effect=failure)
                else:
                    original = Path.write_bytes
                    def write(p, data):
                        if any(part.startswith('restored.restore-') for part in p.parts): raise failure
                        return original(p, data)
                    injection = patch.object(Path, 'write_bytes', write)
                with self.subTest(phase=phase, failure=failure), injection, self.assertLogs('icp_renov_contracts', level='WARNING') as logs:
                    with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
                    self.assertIs(raised.exception.__cause__, failure)
                    self.assertIn('stockage local est insuffisant', raised.exception.user_message)
                    self.assertNotIn('Copiez', raised.exception.user_message)
                    self.assertTrue(any('errno=' in line and 'winerror=' in line for line in logs.output))
                    self.assertTrue(any('errno='+str(failure.errno) in line for line in logs.output))
                    if getattr(failure, 'winerror', None) == 112: self.assertTrue(any('winerror=112' in line for line in logs.output))
                    self.preserved()

    def test_extraction_error_cleans_partial_attempt(self):
        original = zipfile.ZipFile.read
        counts = {}
        def read(z, name, *args, **kwargs):
            counts[name] = counts.get(name, 0)+1
            if name == 'documents/synthetic.txt' and counts[name] == 2: raise OSError(5, 'extraction interrupted')
            return original(z, name, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'read', read):
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
        self.assertEqual(raised.exception.code, 'extraction');self.preserved()

    def test_final_move_failure_restores_existing_empty_directory(self):
        self.target.mkdir()
        original = Path.replace
        def move(p, destination):
            if '.restore-' in p.name: raise PermissionError(13, 'move denied')
            return original(p, destination)
        with patch.object(Path, 'replace', move):
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
        self.assertEqual(raised.exception.code, 'promotion');self.assertFalse(any(self.target.iterdir()))
        self.preserved(target_absent=False)

    def test_new_content_in_empty_target_is_not_deleted(self):
        self.target.mkdir()
        original = BackupService._validate_zip
        def validate(*args):
            result = original(*args);(self.target/'sentinel.txt').write_bytes(b'concurrent');return result
        with patch.object(BackupService, '_validate_zip', side_effect=validate):
            with self.assertRaises(RestoreError): self.service.restore(self.archive, self.target)
        self.assertEqual((self.target/'sentinel.txt').read_bytes(), b'concurrent')
        self.preserved(target_absent=False)

    def test_activation_failure_preserves_previous_config_and_complete_target(self):
        for after_write in (False, True):
            target = self.root/('activation-'+str(after_write))
            original = self.store.save
            def save(value):
                if after_write: original(value)
                raise InvalidBootstrapConfigurationError('save failed')
            with self.subTest(after_write=after_write), patch.object(self.store, 'save', side_effect=save):
                with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, target)
            self.assertEqual(raised.exception.code, 'activation');self.assertEqual(self.store.load(), self.previous)
            self.assertTrue((target/'data'/'icp-renov.sqlite3').is_file())
            self.assertEqual((target/'documents'/'synthetic.txt').read_bytes(), b'synthetic document A')
        self.preserved()

    def test_cleanup_failure_preserves_failure_and_success_and_logs_owned_path(self):
        original = shutil.rmtree
        unrelated = self.root/'unrelated';unrelated.mkdir();(unrelated/'keep').write_bytes(b'keep')
        def cleanup(p, *args, **kwargs):
            if '.icp-restore-source-' in Path(p).name: raise PermissionError(13, 'cleanup locked')
            return original(p, *args, **kwargs)
        with patch.object(module.shutil, 'rmtree', side_effect=cleanup), self.assertLogs('icp_renov_contracts', level='WARNING') as logs:
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.root/'missing', self.target)
            self.assertEqual(raised.exception.code, 'source_copy')
            restored = self.service.restore(self.archive, self.target)
        self.assertEqual(self.store.load().active_workspace, restored.root)
        self.assertTrue(any('cleanup failed' in line and '.icp-restore-source-' in line for line in logs.output))
        self.assertEqual((unrelated/'keep').read_bytes(), b'keep')
        self.assertTrue(all(p.exists() for p in self.stages))

    def test_destination_stage_cleanup_failure_preserves_extraction_error(self):
        original_read = zipfile.ZipFile.read
        original_cleanup = shutil.rmtree
        reads = {}
        def read(z, name, *args, **kwargs):
            reads[name] = reads.get(name, 0)+1
            if name == 'documents/synthetic.txt' and reads[name] == 2:
                raise OSError(5, 'original extraction failure')
            return original_read(z, name, *args, **kwargs)
        def cleanup(p, *args, **kwargs):
            if Path(p).name.startswith('restored.restore-'): raise PermissionError(13, 'locked attempt')
            return original_cleanup(p, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'read', read), patch.object(module.shutil, 'rmtree', side_effect=cleanup), self.assertLogs('icp_renov_contracts', level='WARNING') as logs:
            with self.assertRaises(RestoreError) as raised: self.service.restore(self.archive, self.target)
        self.assertEqual(raised.exception.code, 'extraction')
        self.assertEqual(str(raised.exception.__cause__), '[Errno 5] original extraction failure')
        self.assertEqual(len(list(self.root.glob('restored.restore-*'))), 1)
        self.assertTrue(any('cleanup failed' in text and 'restored.restore-' in text for text in logs.output))
        self.assertEqual(self.store.load(), self.previous);self.assertFalse(self.target.exists())
        self.assertEqual(self.archive.read_bytes(), self.source_bytes)
        self.assertTrue(all(not p.exists() for p in self.stages))


class RestoreUiCompatibilityTests(unittest.TestCase):
    def test_first_use_operational_errors_are_bounded(self):
        for code in ('source_copy','extraction','activation'):
            error = RestoreError(code, 'Message sûr de restauration.')
            restore = Mock(side_effect=error)
            view = SimpleNamespace(context=SimpleNamespace(restore=SimpleNamespace(restore=restore), logger=Mock()))
            with self.subTest(code=code), patch('icp_renov_contracts.ui.main_window.QFileDialog.getOpenFileName', return_value=('archive.icprenovbackup','')), patch('icp_renov_contracts.ui.main_window.QFileDialog.getExistingDirectory', return_value='target'), patch('icp_renov_contracts.ui.main_window.QMessageBox.warning') as warning, patch('icp_renov_contracts.ui.main_window.QMessageBox.information') as success:
                MainWindow._restore_first_use(view)
                self.assertEqual(warning.call_args.args[2], error.user_message);success.assert_not_called()

    @staticmethod
    def box(cancel=False):
        class Box:
            ButtonRole = SimpleNamespace(RejectRole=0, AcceptRole=1)
            def __init__(self, *args): self.confirm = None
            def setWindowTitle(self, value): pass
            def setText(self, value): self.text = value
            def addButton(self, value, role):
                button = object()
                if role == 1: self.confirm = button
                return button
            def exec(self): pass
            def clickedButton(self): return None if cancel else self.confirm
        return Box

    def test_settings_operational_errors_and_confirmation_cancel(self):
        with scratch() as root:
            target = Path(root)/'empty';target.mkdir()
            for code in ('source_copy','extraction','activation','cancel'):
                error = RestoreError(code, 'Message sûr de restauration.')
                restore = Mock(side_effect=error);feedback = Mock()
                view = SimpleNamespace(restore_service=SimpleNamespace(restore=restore), _storage_message=feedback)
                with self.subTest(code=code), patch('icp_renov_contracts.ui.settings_view.QFileDialog.getOpenFileName', return_value=('archive.icprenovbackup','')), patch('icp_renov_contracts.ui.settings_view.QFileDialog.getExistingDirectory', return_value=str(target)), patch('icp_renov_contracts.ui.settings_view.QMessageBox', self.box(code=='cancel')):
                    CompanySettingsView._restore_backup(view)
                    if code == 'cancel': restore.assert_not_called();feedback.assert_not_called()
                    else: feedback.assert_called_once_with(error.user_message)
                self.assertFalse(any(target.iterdir()))

    def test_first_use_archive_cancel_does_not_restore(self):
        restore = Mock();view = SimpleNamespace(context=SimpleNamespace(restore=SimpleNamespace(restore=restore)))
        with patch('icp_renov_contracts.ui.main_window.QFileDialog.getOpenFileName', return_value=('','')):
            MainWindow._restore_first_use(view)
        restore.assert_not_called()
