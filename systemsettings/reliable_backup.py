"""Consistent SQLite snapshot and verified, atomically published packages."""
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.db import connections
from core.storage import atomic_write
from . import backup_scopes as scopes


def verify(package):
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) > 100000 or sum(i.file_size for i in archive.infolist()) > settings.BACKUP_MAX_BYTES:
            raise ValueError('备份超过恢复资源限制')
        if len(names) != len({n.casefold() for n in names}):
            raise ValueError('备份包含重复文件')
        manifests = [n for n in names if n.count('/') == 1 and n.endswith('/备份清单.json')]
        if len(manifests) != 1:
            raise ValueError('备份清单缺失或不唯一')
        if archive.getinfo(manifests[0]).file_size > 16 * 1024**2:
            raise ValueError('备份清单超出限制')
        manifest = json.loads(archive.read(manifests[0]))
        if manifest.get('format_version') != 2 or manifest.get('app') != 'zhishiku-platform':
            raise ValueError('不支持的备份格式')
        prefix = manifests[0].split('/')[0] + '/'
        if prefix.startswith(('/', '\\')) or ':' in prefix or '..' in prefix or '\\' in prefix:
            raise ValueError('备份根路径不安全')
        expected = {prefix + rel for rel in manifest['files']}
        actual = {n for n in names if not n.endswith('/')} - {manifests[0]}
        if expected != actual:
            raise ValueError('文件清单与备份内容不一致')
        for relative, spec in manifest['files'].items():
            if Path(relative).is_absolute() or '..' in Path(relative).parts or '\\' in relative or ':' in relative:
                raise ValueError('备份路径不安全')
            digest = hashlib.sha256()
            size = 0
            with archive.open(prefix + relative) as source:
                for chunk in iter(lambda: source.read(1024**2), b''):
                    digest.update(chunk)
                    size += len(chunk)
            if digest.hexdigest() != spec['sha256'] or size != spec['size']:
                raise ValueError('备份文件校验失败：' + relative)
        return manifest


def snapshot_database(destination):
    database = connections['default']
    database.ensure_connection()
    with closing(sqlite3.connect(destination)) as target:
        database.connection.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('数据库快照完整性检查失败')
        # External credentials are machine-specific. Legacy secrets are removed from copies.
        for key, value in target.execute('SELECT key, value FROM core_systemconfig').fetchall():
            if key in {'gitee_token', 'dashscope_api_key'}:
                target.execute('UPDATE core_systemconfig SET value=? WHERE key=?', ('', key))
            elif key == 'ai_providers':
                providers = json.loads(value or '[]')
                for provider in providers:
                    provider['api_key'] = ''
                target.execute('UPDATE core_systemconfig SET value=? WHERE key=?', (json.dumps(providers), key))
        target.commit()


def dump_snapshot(snapshot, model_labels, target_dir):
    """Serialize only the immutable snapshot; never reread live ORM tables."""
    from django.apps import apps
    from django.core.serializers.json import DjangoJSONEncoder
    from django.db.models import JSONField
    records = []
    with closing(sqlite3.connect(snapshot)) as connection:
        connection.row_factory = sqlite3.Row
        for label in model_labels:
            model = apps.get_model(label)
            fields = model._meta.local_fields
            table = model._meta.db_table.replace('"', '""')
            for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                values = {}
                for field in fields:
                    if field.primary_key:
                        continue
                    raw = row[field.column]
                    values[field.name] = json.loads(raw) if isinstance(field, JSONField) and raw is not None else field.to_python(raw)
                records.append({'model': model._meta.label_lower, 'pk': row[model._meta.pk.column], 'fields': values})
    filename = '知识数据.json'
    text = json.dumps(records, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2)
    path = Path(target_dir) / filename
    path.write_text(text, encoding='utf-8')
    return filename, path.stat().st_size


def build(kind, on_progress=None, options=None):
    from .backup_engine import _walk, _skip_paths_for, _skeleton_dirs, _dump_database
    from .backup_install import generated_files
    spec = scopes.scope_spec(kind)
    options = scopes.merge_options(kind, options)
    root = Path(settings.BACKUP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    package = f'{kind}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:12]}'
    final = root / (package + '.zip')
    maximum = settings.BACKUP_MAX_BYTES
    temporary = root / ('.' + package + '.partial')
    files = {}
    processed = 0
    def report(progress, stage):
        if on_progress:
            on_progress(progress, stage)
    try:
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=root) as workspace:
            workspace = Path(workspace)
            snapshot = workspace / 'db.sqlite3'
            if spec['db_dump'] or spec['embed_db']:
                report(2, '创建并校验数据库快照')
                snapshot_database(snapshot)
            dump_file = None
            if spec['db_dump']:
                fn, _ = _dump_database(spec['db_dump'], workspace, snapshot_path=snapshot)
                dump_file = workspace / fn
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                def put(relative, source=None, data=None):
                    nonlocal processed
                    if relative in files:
                        raise RuntimeError('备份路径冲突：' + relative)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(package + '/' + relative, 'w', force_zip64=True) as output:
                        if data is not None:
                            chunks = [data]
                            handle = None
                        else:
                            handle = open(source, 'rb')
                            chunks = iter(lambda: handle.read(1024**2), b'')
                        try:
                            for chunk in chunks:
                                size += len(chunk)
                                processed += len(chunk)
                                if processed > maximum or shutil.disk_usage(root).free < 16 * 1024**2:
                                    raise RuntimeError('备份超出资源上限或磁盘空间不足')
                                digest.update(chunk)
                                output.write(chunk)
                                report(min(95, 5 + len(files) // 10), '打包并计算完整性校验')
                        finally:
                            if handle:
                                handle.close()
                    files[relative] = {'size': size, 'sha256': digest.hexdigest()}
                if dump_file:
                    put('database/知识数据.json', source=dump_file)
                if spec['embed_db']:
                    put('XiTong/db.sqlite3', source=snapshot)
                for relative, text in generated_files(kind, package):
                    put(relative, data=text.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8'))
                for src in spec['srcs']:
                    source_root = Path(src['path'])
                    if not source_root.exists():
                        # An empty instance may not have a data/media directory yet.
                        if src['filter'] == 'data':
                            continue
                        raise RuntimeError('必要备份目录不存在')
                    for path, relative, _, is_dir in _walk(source_root, src['filter'], skip_under=root,
                                                         skip_paths=_skip_paths_for(kind, options)):
                        if is_dir:
                            continue
                        if path.resolve() == Path(settings.DATABASES['default']['NAME']).resolve():
                            continue
                        arc = src['dest'] + '/' + relative
                        # Generated launchers are authoritative and deliberately replace source launchers.
                        if arc in files:
                            continue
                        put(arc, source=path)
                if kind in {'knowledge', 'migration'}:
                    # Validate against the same snapshot, not a changing live asset table.
                    with closing(sqlite3.connect(snapshot)) as database:
                        for path, digest, name in database.execute('SELECT storage_path, sha256, original_name FROM core_asset'):
                            arc = ('media/' if kind == 'knowledge' else 'XiTong/media/') + path
                            if arc not in files or files[arc]['sha256'] != digest:
                                raise RuntimeError('必要原件缺失或内容改变：' + name)
                for directory in _skeleton_dirs(spec):
                    archive.writestr(package + '/' + directory, b'')
                manifest = {'app': 'zhishiku-platform', 'format_version': 2,
                    'backup_kind': kind, 'created': datetime.now().isoformat(),
                    'files': files, 'credentials': 'excluded; reconfigure on target',
                    'source_roots': {'code': str(settings.BASE_DIR), 'data': str(settings.ZHI_SHI_ROOT),
                                     'media': str(settings.MEDIA_ROOT)},
                    'consistency': 'SQLite snapshot; immutable assets; filesystem changes during backup must be avoided'}
                archive.writestr(package + '/备份清单.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            report(97, '验证备份包')
            verify(temporary)
            if temporary.stat().st_size > maximum:
                raise RuntimeError('备份包超出大小限制')
            os.replace(temporary, final)
            atomic_write(root / (package + '.manifest.json'), json.dumps(manifest, ensure_ascii=False, indent=2))
        report(100, '已完成并通过文件完整性校验')
        return {'kind': kind, 'title': spec['title'], 'package_name': package,
            'package_path': str(final), 'package_size': final.stat().st_size,
            'file_count': len(files), 'skipped': [], 'duplicates': [], 'warnings': [],
            'stats': {'files': len(files), 'bytes': processed}, 'created': manifest['created']}
    except Exception:
        temporary.unlink(missing_ok=True)
        # A metadata publication failure must not leave an apparently usable package.
        final.unlink(missing_ok=True)
        raise
