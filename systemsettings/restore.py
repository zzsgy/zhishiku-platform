"""Restore into a new directory; an existing installation is never overwritten."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from django.conf import settings
from core.storage import bounded_path
from .reliable_backup import verify


def relocate(database, old_roots, target):
    mappings = [(str(old_roots.get(name, '')).replace('\\', '/').rstrip('/'), str(path))
                for name, path in [('media', target / 'XiTong/media'),
                                   ('data', target / 'ZhiShi'), ('code', target / 'XiTong')]]
    unresolved = []
    with closing(sqlite3.connect(database)) as connection:
        for table, fields in [('bookshelf_book', ['file_path', 'cover']),
                              ('core_workfileindex', ['original_path']),
                              ('office_advise', ['file_path']), ('office_report', ['file_path'])]:
            for field in fields:
                for pk, value in connection.execute(f'SELECT id, {field} FROM {table}').fetchall():
                    if not value or not (Path(value).is_absolute() or ':/' in value.replace('\\', '/')):
                        continue
                    normalized = value.replace('\\', '/')
                    for before, after in mappings:
                        if before and normalized.lower().startswith(before.lower() + '/'):
                            suffix = normalized[len(before) + 1:]
                            new_path = bounded_path(after, suffix)
                            connection.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (str(new_path), pk))
                            break
                    else:
                        unresolved.append({'table': table, 'id': pk, 'field': field,
                                           'reason': '外部工作目录需在新电脑重新指定；原路径已保留'})
        # Jobs and backup packages refer to processes/files on the original machine.
        connection.execute("UPDATE core_job SET status='failed', active_key=NULL, error='迁移后需重新提交' WHERE status IN ('pending','running')")
        connection.execute("UPDATE systemsettings_backupjob SET status='failed', message='原机备份记录，迁移后请重新备份' WHERE status IN ('pending','running')")
        connection.commit()
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('恢复数据库完整性检查失败')
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('恢复数据库关系检查失败')
    return unresolved


def restore(package, destination):
    manifest = verify(package)
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('恢复目标必须是尚不存在的新目录，不允许覆盖现有平台')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'instance'
        staging.mkdir()
        with zipfile.ZipFile(package) as archive:
            prefix = next(n for n in archive.namelist() if n.endswith('/备份清单.json')).split('/')[0] + '/'
            for relative in manifest['files']:
                target = bounded_path(staging, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(prefix + relative) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output, 1024**2)
        app = staging / 'XiTong'
        if manifest['backup_kind'] == 'knowledge':
            from .backup_engine import _walk
            app.mkdir(exist_ok=True)
            for source, relative, _, is_directory in _walk(settings.BASE_DIR, 'code'):
                if is_directory or source.suffix == '.sqlite3':
                    continue
                target = bounded_path(app, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            if (staging / 'media').exists():
                shutil.move(str(staging / 'media'), str(app / 'media'))
        database = app / 'db.sqlite3'
        env = {**os.environ, 'ZHISHIKU_DB_PATH': str(database), 'ZHISHIKU_MEDIA_ROOT': str(app / 'media'),
               'ZHISHIKU_DATA_ROOT': str(staging / 'ZhiShi'), 'ZHISHIKU_NETWORK_MODE': '0',
               'ZHISHIKU_SECRET_KEY': 'isolated-restore-validation-only'}
        def manage(*arguments):
            result = subprocess.run([sys.executable, '-X', 'utf8', str(app / 'manage.py'), *arguments],
                                    cwd=app, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
            if result.returncode:
                raise RuntimeError('恢复验证失败：' + result.stderr[-1000:])
        manage('migrate', '--noinput')
        if manifest['backup_kind'] == 'knowledge':
            manage('loaddata', str(staging / 'database/知识数据.json'))
        unresolved = relocate(database, manifest.get('source_roots', {}), destination)
        manage('check')
        # Do not copy credentials or original absolute configuration to the new instance.
        (app / '.instance-secret').unlink(missing_ok=True)
        report = {'status': 'verified', 'kind': manifest['backup_kind'], 'unresolved_external_paths': unresolved,
                  'credentials': '需重新配置', 'accounts': '知识/系统包需创建管理员；迁移包保留原账号'}
        (staging / '恢复验证.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if destination.exists():
            raise ValueError('恢复目标在验证期间被创建，请换一个新目录')
        os.rename(staging, destination)
    return report
