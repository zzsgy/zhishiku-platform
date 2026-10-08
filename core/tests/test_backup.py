import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock
from django.test import TransactionTestCase, override_settings
from django.conf import settings
from core.models import SystemConfig
from systemsettings.reliable_backup import build, verify, snapshot_database
from systemsettings import backup_scopes as scopes
from systemsettings.backup_engine import _excluded_file, _excluded_dir


class BackupTests(TransactionTestCase):
    def test_worker_finishes_a_real_verified_backup(self):
        from core.jobs import enqueue, run_one
        from systemsettings.models import BackupJob
        backup=BackupJob.objects.create(kind='knowledge')
        job=enqueue('backup',{'backup_id':backup.pk,'kind':'knowledge','options':{}},key='backup-active')
        self.assertTrue(run_one())
        job.refresh_from_db()
        backup.refresh_from_db()
        self.assertEqual(job.status,'done')
        self.assertEqual(backup.status,'done')
        verify(backup.package_path)

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.code, self.data, self.media = [self.root / name for name in ('code', 'data', 'media')]
        for directory in (self.code, self.data, self.media):
            directory.mkdir()
        (self.code / 'manage.py').write_text('# source')
        (self.code / '.env').write_text('TOKEN=fake-secret')
        (self.data / 'sample.md').write_text('备份正文', encoding='utf-8')
        self.settings_override = override_settings(BACKUP_ROOT=self.root / 'backup', ZHI_SHI_ROOT=self.data,
                                                   MEDIA_ROOT=self.media, BACKUP_MAX_BYTES=50 * 1024**2)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        specs = copy.deepcopy(scopes.SCOPES)
        for spec in specs.values():
            spec['srcs'] = [{'path': self.code, 'dest': 'XiTong', 'filter': 'code' if not spec['embed_db'] else 'code_with_data'}]
            if spec['kind'] != 'system':
                spec['srcs'] += [{'path': self.data, 'dest': 'ZhiShi', 'filter': 'data'},
                                 {'path': self.media, 'dest': 'media' if spec['kind'] == 'knowledge' else 'XiTong/media', 'filter': 'data'}]
        self.spec_patch = patch.object(scopes, 'SCOPES', specs)
        self.spec_patch.start()
        self.addCleanup(self.spec_patch.stop)

    def test_package_is_verified_and_excludes_credentials(self):
        SystemConfig.set_value('gitee_token', 'fake-legacy-secret')
        result = build('migration')
        manifest = verify(result['package_path'])
        self.assertEqual(manifest['format_version'], 2)
        with zipfile.ZipFile(result['package_path']) as archive:
            self.assertFalse(any(n.endswith('/.env') for n in archive.namelist()))
            database_name = next(n for n in archive.namelist() if n.endswith('/XiTong/db.sqlite3'))
            database = self.root / 'restored.sqlite3'
            database.write_bytes(archive.read(database_name))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute("SELECT value FROM core_systemconfig WHERE key='gitee_token'").fetchone()[0], '')

    def test_tampered_file_is_detected(self):
        result = build('knowledge')
        package = Path(result['package_path'])
        with zipfile.ZipFile(package) as source:
            contents = [(n, source.read(n)) for n in source.namelist()]
        with zipfile.ZipFile(package, 'w') as target:
            for name, content in contents:
                target.writestr(name, b'tampered' if name.endswith('/sample.md') else content)
        with self.assertRaises(ValueError):
            verify(package)

    def test_database_export_failure_leaves_no_package(self):
        with patch('systemsettings.backup_engine._dump_database', side_effect=RuntimeError('export failure')):
            with self.assertRaises(RuntimeError):
                build('knowledge')
        self.assertFalse(list((self.root / 'backup').glob('*.zip')))
        self.assertFalse(list((self.root / 'backup').glob('*.partial')))

    def test_resource_limit_prevents_success(self):
        with override_settings(BACKUP_MAX_BYTES=20):
            with self.assertRaises(RuntimeError):
                build('system')
        self.assertFalse(list((self.root / 'backup').glob('*.zip')))

    def test_restore_into_new_directory_relocates_original_and_reads_content(self):
        from core.models import KnowledgeBase, KnowledgeNode
        from core.storage import save_upload
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bookshelf.models import Book
        from systemsettings.restore import restore
        from django.core import management
        import subprocess
        import sys
        import os
        original, asset = save_upload(SimpleUploadedFile('原始资料.txt', '换目录恢复后仍可阅读'.encode()))
        base = KnowledgeBase.objects.create(name='恢复测试库', directory='knowledge')
        KnowledgeNode.objects.create(base=base, title='恢复测试知识', content_md='保留正文', asset=asset)
        Book.objects.create(title='恢复测试书籍', file_path=str(original), asset=asset)
        result = build('knowledge')
        target = self.root / '新目录 with spaces'
        report = restore(result['package_path'], target)
        self.assertEqual(report['status'], 'verified')
        db = target / 'XiTong/db.sqlite3'
        with closing(sqlite3.connect(db)) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(restored.execute('SELECT content_md FROM core_knowledgenode').fetchone()[0], '保留正文')
            relocated = Path(restored.execute('SELECT file_path FROM bookshelf_book').fetchone()[0])
        self.assertTrue(relocated.is_relative_to(target))
        self.assertEqual(relocated.read_text(encoding='utf-8'), '换目录恢复后仍可阅读')
        with self.assertRaises(ValueError):
            restore(result['package_path'], target)

    def test_missing_original_prevents_publishing_package(self):
        from core.models import Asset
        Asset.objects.create(original_name='缺失原件', storage_path='assets/missing.txt', sha256='f'*64, size=3)
        with self.assertRaisesRegex(RuntimeError, '原件缺失'):
            build('knowledge')
        self.assertFalse(list((self.root / 'backup').glob('*.zip')))

    def test_snapshot_contains_wal_commits_during_writes(self):
        live = self.root / 'live.sqlite3'
        with closing(sqlite3.connect(live)) as setup:
            setup.execute('PRAGMA journal_mode=WAL')
            setup.execute('CREATE TABLE core_systemconfig(key TEXT, value TEXT)')
            setup.execute('CREATE TABLE evidence(value INTEGER)')
            setup.execute('INSERT INTO evidence VALUES(1)')
            setup.commit()
        stopped = threading.Event()
        def writer():
            with closing(sqlite3.connect(live)) as connection:
                for value in range(2, 100):
                    if stopped.is_set():
                        break
                    connection.execute('INSERT INTO evidence VALUES(?)', (value,))
                    connection.commit()
                    time.sleep(0.005)
        thread = threading.Thread(target=writer)
        thread.start()
        source = sqlite3.connect(live)
        adapter = Mock(connection=source)
        try:
            with patch('systemsettings.reliable_backup.connections', {'default': adapter}):
                snapshot_database(self.root / 'snapshot.sqlite3')
        finally:
            stopped.set()
            thread.join()
            source.close()
        with closing(sqlite3.connect(self.root / 'snapshot.sqlite3')) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertGreaterEqual(restored.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)

    def test_default_filter_excludes_runtime_secrets_and_temp(self):
        for name in ('.env', '.env.local', '.instance-secret'):
            self.assertTrue(_excluded_file(name, 'code'))
        for name in ('temp', 'tmp', 'work', '.runtime'):
            self.assertTrue(_excluded_dir(name, 'code'))
