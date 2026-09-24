"""备份引擎：范围预演（扫描） + ZIP 打包 + 清单/说明生成。

拆分原则：
  * 范围从哪来 —— 一律问 backup_scopes（唯一事实源），本文件不重复定义范围；
  * 打包怎么压 —— 文本/代码用 DEFLATE，已压缩媒体（图片/视频/压缩包/exe）用 STORED，
                  避免对不可压缩内容做无谓的 CPU 消耗；
  * 进度怎么报 —— 通过 `on_progress(percent, stage)` 回调交给调用方落库，引擎不碰视图。
"""
import json
import os
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

from . import backup_install
from . import backup_scopes as S

# 已经压缩过的格式：再 DEFLATE 收益极低，直接原样存储，速度更快。
_STORE_SUFFIXES = (
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ico', '.heic',
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.mp3', '.m4a', '.wav', '.flac',
    '.zip', '.7z', '.rar', '.gz', '.bz2', '.xz', '.pdf', '.docx', '.xlsx',
    '.pptx', '.exe', '.dll', '.whl', '.apk', '.jar',
)

# 包名前缀（中文可出现在文件名里，不影响脚本执行）
PACKAGE_PREFIX = {
    'knowledge': '知识备份',
    'system': '系统空平台',
    'migration': '迁移整包',
}


# ---------------------------------------------------------------------------
# 排除判定
# ---------------------------------------------------------------------------
def _excluded_dir(name, mode):
    """目录是否整体跳过。"""
    if name in S.CODE_EXCLUDE_DIRS:
        # code 模式不带走上传媒体内容（空目录由骨架补上）；
        # code_with_data（迁移包）与 data 模式则保留 media。
        if name == 'media' and mode == 'code_with_data':
            return False
        return True
    if name.startswith(S.CODE_EXCLUDE_PREFIXES):
        return True
    return False


def _excluded_file(name, mode):
    """文件是否跳过。"""
    if name in S.CODE_EXCLUDE_FILES:
        return True
    if name.startswith(S.CODE_EXCLUDE_PREFIXES):
        return True
    low = name.lower()
    if low.endswith(S.CODE_EXCLUDE_SUFFIXES):
        return True
    if mode == 'code' and name in S.SYSTEM_ONLY_EXCLUDE_FILES:
        return True
    return False


def _walk(root, mode, skip_under=None, skip_paths=None):
    """遍历 root，按 mode 过滤，产出 (绝对路径, 相对 root 的 Posix 相对路径, 字节数, 是否目录)。

    目录也会产出（字节数为 0）——打包时必须显式写入目录条目，否则**空目录会在
    解压后消失**（media/uploads 之类初期为空的目录就会丢）。
    只读取目录项元数据；单个条目出错（权限/占用）时跳过并继续，不中断整次备份。
    skip_paths 用于按用户选项排除个别文件（如体积很大的 ffmpeg.exe）。
    """
    root = Path(root)
    if not root.exists():
        return
    skip_under = Path(skip_under) if skip_under else None
    skip_paths = {Path(p) for p in (skip_paths or ())}
    stack = [(root, '')]
    while stack:
        cur, rel = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            epath = Path(e.path)
            if epath in skip_paths:
                continue
            if skip_under is not None:
                try:
                    if epath.is_relative_to(skip_under):
                        continue
                except (ValueError, AttributeError):
                    pass
            child_rel = f'{rel}/{e.name}' if rel else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if mode != 'data' and _excluded_dir(e.name, mode):
                        continue
                    yield epath, child_rel, 0, True
                    stack.append((epath, child_rel))
                elif e.is_file(follow_symlinks=False):
                    if mode != 'data' and _excluded_file(e.name, mode):
                        continue
                    yield epath, child_rel, e.stat().st_size, False
            except OSError:
                continue


def _skip_paths_for(kind, options):
    """用户选项 -> 需要跳过的绝对路径集合。"""
    skip = set()
    if kind in ('system', 'migration') and not options.get('include_ffmpeg', True):
        skip.add(S.CODE_ROOT / 'bin' / 'ffmpeg.exe')
    return skip


# ---------------------------------------------------------------------------
# 范围预演（只读扫描，不产生任何文件）
# ---------------------------------------------------------------------------
def _skeleton_dirs(spec):
    """包内需要预建的空目录（相对包根，以 / 结尾）。

    只补「源头走不到」的目录：知识目录若本身就是要打包的数据源（迁移包），
    它的目录条目会由数据遍历自然产出，这里再补就成了重复条目。
    """
    out = []
    dests = {s['dest'] for s in spec['srcs']}
    if spec['with_skeleton'] and 'ZhiShi' not in dests:
        for rel in S.zhi_shi_skeleton_dirs():
            out.append(f'ZhiShi/{rel}/')
    # 日志目录在打包时被排除，但 settings.py 一启动就要往里写（否则 Django
    # 会在 dictConfig 阶段抛 "Unable to configure handler 'file'"）。settings
    # 自己会 mkdir，这里再补一个空目录，双保险。
    if spec['with_skeleton']:
        out.append('XiTong/logs/')
    if spec['kind'] == 'system':
        # 系统包清空了 media 内容（media 整个被排除），目录结构得手工补
        for sub in ('uploads', 'books', 'videos', 'videos/tmp'):
            out.append(f'XiTong/media/{sub}/')
    return out


def scan(kind, options=None):
    """只读预演：返回文件数、体积、逐项明细与告警。用于页面展示「本次会打包什么」。"""
    spec = S.scope_spec(kind)
    options = S.merge_options(kind, options)
    skip_paths = _skip_paths_for(kind, options)
    items = []
    warnings = []
    total_files = 0
    total_bytes = 0
    total_dirs = 0
    gen = backup_install.generated_files(kind)

    for src in spec['srcs']:
        root = Path(src['path'])
        label = src.get('label') or src['dest']
        n = 0
        nd = 0
        b = 0
        if not root.exists():
            warnings.append(f'{label}：路径不存在 {root}')
        else:
            for _p, _rel, size, is_dir in _walk(root, src['filter'],
                                                skip_under=S.BACKUP_ROOT,
                                                skip_paths=skip_paths):
                if is_dir:
                    nd += 1
                    continue
                n += 1
                b += size
        items.append({'label': label, 'dest': src['dest'], 'path': str(root),
                      'files': n, 'dirs': nd, 'bytes': b, 'missing': not root.exists()})
        total_files += n
        total_dirs += nd
        total_bytes += b

    # 随包生成的脚本 / 说明 / 清单
    gen_bytes = sum(len(t.encode('utf-8')) for _rel, t in gen)
    gen_files = len(gen)
    items.append({'label': '安装脚本与说明（随包生成）', 'dest': 'install/ · VERSION.txt',
                  'path': '(生成)', 'files': gen_files, 'dirs': 0, 'bytes': gen_bytes,
                  'missing': False})
    total_files += gen_files
    total_bytes += gen_bytes

    db_note = None
    if spec['db_dump']:
        db_note = f'数据库导出：{len(spec["db_dump"])} 张知识数据表（JSON，体积通常 < 5 MB）'
    elif spec['embed_db']:
        db_note = '数据库随包：db.sqlite3 原文件完整嵌入'

    return {
        'kind': kind,
        'title': spec['title'],
        'files': total_files,
        'dirs': total_dirs,
        'bytes': total_bytes,
        'items': items,
        'warnings': warnings,
        'skeleton_dirs': len(_skeleton_dirs(spec)),
        'db_note': db_note,
        'options': options,
        'too_big': total_bytes > settings.BACKUP_MAX_BYTES,
        'max_bytes': settings.BACKUP_MAX_BYTES,
    }


# ---------------------------------------------------------------------------
# 生成包内说明文档
# ---------------------------------------------------------------------------
def _fmt_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.2f} {unit}'
        n /= 1024.0


def _to_crlf(text):
    """包内生成的文本统一用 CRLF：Windows 记事本打开中文说明不会挤成一行。"""
    return text.replace('\r\n', '\n').replace('\n', '\r\n')


def _manifest_doc(spec, stats, created):
    """备份清单.json —— 机器可读，便于日后核对与自动化校验。"""
    return json.dumps({
        'app': '智识库知识平台',
        'backup_kind': spec['kind'],
        'backup_title': spec['title'],
        'backup_subtitle': spec['subtitle'],
        'created_at': created.strftime('%Y-%m-%d %H:%M:%S'),
        'source_machine': {
            'code_root': str(S.CODE_ROOT),
            'data_root': str(S.DATA_ROOT),
        },
        'scope': {
            'includes': spec['includes'],
            'excludes': spec['excludes'],
            'database_tables_dumped': spec['db_dump'],
            'database_file_embedded': spec['embed_db'],
        },
        'content': {
            'file_count': stats['files'],
            'total_bytes': stats['bytes'],
            'breakdown': [
                {k: v for k, v in it.items()} for it in stats['items']
            ],
        },
        'restore_hint': spec['restore_hint'],
    }, ensure_ascii=False, indent=2)


def _readme_doc(spec, stats, created, pkg_name):
    """备份说明.md —— 人类可读，中文，含范围差异对照与恢复步骤。"""
    L = []
    L.append(f'# {spec["title"]} · {pkg_name}')
    L.append('')
    L.append(f'> 生成时间：{created:%Y-%m-%d %H:%M:%S}  ')
    L.append(f'> 生成位置：{S.CODE_ROOT}  ')
    L.append(f'> 包内文件数：{stats["files"]}　体积：{_fmt_size(stats["bytes"])}')
    L.append('')
    L.append('## 这个包是做什么的')
    L.append('')
    L.append(spec['purpose'])
    L.append('')
    L.append('## 包含什么')
    L.append('')
    for x in spec['includes']:
        L.append(f'- ✅ {x}')
    L.append('')
    L.append('## 不包含什么')
    L.append('')
    for x in spec['excludes']:
        L.append(f'- ⛔ {x}')
    L.append('')
    L.append('## 三类备份的范围差异（对照）')
    L.append('')
    L.append('| 备份类型 | 程序代码 | 依赖环境 | 知识资料 | 数据库 | 用途 |')
    L.append('|---|---|---|---|---|---|')
    L.append('| 知识备份 | ✗ | ✗ | ✅ 全部 | 导出为 JSON | 只留知识，随时找回 |')
    L.append('| 系统备份 | ✅ 全部 | ✅ 由脚本重建 | ✗ 零知识 | 新建空库 | 发给他人装空平台 |')
    L.append('| 迁移备份 | ✅ 全部 | ✅ 由脚本重建 | ✅ 全部 | ✅ 原文件 | 换电脑整体搬迁 |')
    L.append('')
    L.append('## 包内结构')
    L.append('')
    L.append(_tree_doc(spec, pkg_name))
    L.append('')
    L.append('## 怎么用')
    L.append('')
    L.append(spec['restore_hint'])
    L.append('')
    L.append('## 本次内容明细')
    L.append('')
    L.append('| 内容 | 包内位置 | 文件数 | 目录数 | 体积 |')
    L.append('|---|---|---|---|---|')
    for it in stats['items']:
        L.append(f'| {it["label"]} | `{it["dest"]}` | {it["files"]} | {it.get("dirs", 0)} | '
                 f'{_fmt_size(it["bytes"])} |')
    L.append('')
    if spec['db_dump']:
        L.append(f'数据库导出表（{len(spec["db_dump"])} 张）：')
        L.append('')
        for m in spec['db_dump']:
            L.append(f'- `{m}`')
        L.append('')
    if stats['warnings']:
        L.append('## 生成时的告警')
        L.append('')
        for w in stats['warnings']:
            L.append(f'- ⚠️ {w}')
        L.append('')
    L.append('---')
    L.append('')
    L.append('> 提示：包内中文文件名较多，若用 Windows 自带解压出现乱码，'
             '请改用 7-Zip 或 WinRAR 解压。')
    L.append('')
    return '\n'.join(L)


def _tree_doc(spec, pkg_name):
    kind = spec['kind']
    lines = ['```', f'{pkg_name}/']
    if kind == 'knowledge':
        lines += [
            '├─ ZhiShi/            ← 知识库文件（覆盖回 <平台>/ZhiShi）',
            '├─ media/             ← 上传媒体原件（覆盖回 <平台>/XiTong/media）',
            '├─ database/',
            '│   └─ 知识数据.json   ← 知识数据表导出（可读、可回填）',
            '├─ 备份清单.json',
            '├─ 备份说明.md',
            '└─ 恢复知识.cmd        ← 双击即可把知识数据恢复到平台目录',
        ]
    else:
        data_line = ('├─ ZhiShi/            ← 知识库目录（含全部知识资料）'
                     if kind == 'migration' else
                     '├─ ZhiShi/            ← 知识库目录（空骨架）')
        lines += [
            '├─ XiTong/            ← 平台程序（含 requirements.txt、启动脚本）',
            '│   ├─ manage.py   kb/   core/   11 个功能模块 …',
            '│   ├─ bin/ffmpeg.exe',
            '│   └─ media/         ← 媒体目录',
            data_line,
        ]
        if kind == 'migration':
            lines.append('│                      （含 db.sqlite3 原文件与 database/ 导出）')
        lines += [
            '├─ install/',
            '│   ├─ install.cmd        ← 一键安装脚本（检测 Python → 建环境 → 装依赖 → 建库）',
            '│   ├─ start-platform.cmd ← 启动脚本',
            '│   └─ stats.py',
            '├─ 一键安装.cmd        ← 双击这一个就够了',
            '├─ 启动平台.cmd',
            '├─ 安装说明.md',
            '├─ 备份清单.json 备份说明.md',
            '└─ VERSION.txt',
        ]
    lines.append('```')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 打包
# ---------------------------------------------------------------------------
def package_name(kind):
    spec = S.scope_spec(kind)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'{PACKAGE_PREFIX.get(kind, spec["title"])}_{ts}'


def _dump_database(models, target_dir):
    """把指定的数据表导出成 JSON（dumpdata）。返回 (文件名, 字节数) 或 (None, 0)。"""
    from io import StringIO
    from django.core.management import call_command

    buf = StringIO()
    call_command('dumpdata', *models, indent=2, stdout=buf, verbosity=0)
    text = buf.getvalue() or '[]'
    target_dir.mkdir(parents=True, exist_ok=True)
    fn = '知识数据.json'
    (Path(target_dir) / fn).write_text(text, encoding='utf-8')
    return fn, len(text.encode('utf-8'))


def build(kind, on_progress=None, options=None):
    """执行一次打包，返回结果字典。

    on_progress(percent:int, stage:str) —— 可选；调用方用它把进度落库。
    本函数不写数据库，只负责在磁盘上产出 ZIP，便于单独测试。
    """
    spec = S.scope_spec(kind)
    options = S.merge_options(kind, options)
    skip_paths = _skip_paths_for(kind, options)
    stats = scan(kind, options)
    if stats['too_big']:
        raise RuntimeError(
            '预计体积 %s 超过单包上限 %s，已中止。如需放开请在环境变量 '
            'ZHISHIKU_BACKUP_MAX_BYTES 中调整。' % (_fmt_size(stats['bytes']),
                                                  _fmt_size(stats['max_bytes'])))

    def report(pct, stage):
        if on_progress:
            try:
                on_progress(int(pct), stage)
            except Exception:
                pass

    created = datetime.now()
    S.BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    pkg = package_name(kind)
    zip_path = Path(S.BACKUP_ROOT) / f'{pkg}.zip'

    # 数据库导出（知识包 / 迁移包才有）——先导出，便于随后一并写入包内
    db_temp = Path(S.BACKUP_ROOT) / f'.{pkg}.dbdump'
    db_files = []
    if spec['db_dump']:
        report(3, '导出数据库知识数据')
        try:
            fn, size = _dump_database(spec['db_dump'], db_temp)
            if fn:
                # 目录名保持 ASCII，脚本才能直接引用；文件名可中文（恢复脚本用通配匹配）
                db_files.append((Path(db_temp) / fn, f'database/{fn}', size))
        except Exception as e:  # 导出失败不阻断主流程，但如实记录
            stats['warnings'].append(f'数据库导出失败：{e}')
    report(5, '准备打包')

    # 随包生成的脚本与说明（备份清单里的统计数需要先并入 stats）
    readme = _readme_doc(spec, stats, created, pkg)
    manifest = _manifest_doc(spec, stats, created)
    gen = backup_install.generated_files(kind, pkg)
    gen.append(('备份清单.json', manifest))
    gen.append(('备份说明.md', readme))

    total = max(1, stats['bytes'])
    done = 0
    written = 0
    skipped = []
    duplicates = []
    last_report = 0.0

    store = zipfile.ZIP_STORED
    deflate = zipfile.ZIP_DEFLATED

    with zipfile.ZipFile(zip_path, 'w', compression=deflate, compresslevel=6,
                         allowZip64=True) as zf:
        written_arcs = set()

        def put(arc, data):
            """写入一个生成条目；同名只保留第一次。

            防御性措施：同名条目会让解压结果依赖解压器的实现（覆盖或报错），
            目录条目与随包文档偶尔会与文件树重叠，这里统一兜住并记账。
            """
            if arc in written_arcs:
                duplicates.append(arc)
                return False
            written_arcs.add(arc)
            zf.writestr(arc, data, compress_type=deflate)
            return True

        # 1) 空目录骨架（zip 中显式写入以 / 结尾的条目）
        if spec['with_skeleton']:
            # 系统包的知识目录是空的，补一份目录说明；迁移包的真实 README.md 会随数据
            # 一起带走，这里再写就会产生同名的重复条目，因此只在系统包里补。
            if spec['kind'] == 'system':
                if put(f'{pkg}/ZhiShi/README.md',
                       _to_crlf(_zhi_shi_readme()).encode('utf-8')):
                    written += 1
            for d in _skeleton_dirs(spec):
                put(f'{pkg}/{d}', b'')

        # 2) 数据库导出
        for src, arc, size in db_files:
            full = f'{pkg}/{arc}'
            if full in written_arcs:
                duplicates.append(full)
                continue
            try:
                zf.write(src, full, compress_type=deflate if size > 4096 else store)
            except OSError as e:
                skipped.append(f'{src}: {e}')
                continue
            written_arcs.add(full)
            done += size
            written += 1

        # 3) 随包生成的脚本与说明
        for rel, text in gen:
            if put(f'{pkg}/{rel}', _to_crlf(text).encode('utf-8')):
                done += len(text)
                written += 1

        # 4) 文件树
        for src in spec['srcs']:
            root = Path(src['path'])
            dest = src['dest']
            for path, rel, size, is_dir in _walk(root, src['filter'],
                                                 skip_under=S.BACKUP_ROOT,
                                                 skip_paths=skip_paths):
                arc = f'{pkg}/{dest}/{rel}'
                if is_dir:
                    # 显式目录条目，保证空目录解压后依然存在
                    put(arc + '/', b'')
                    continue
                if arc in written_arcs:
                    duplicates.append(arc)
                    continue
                ctype = store if path.suffix.lower() in _STORE_SUFFIXES else deflate
                try:
                    zf.write(path, arc, compress_type=ctype)
                except (OSError, zipfile.BadZipFile, ValueError) as e:
                    skipped.append(f'{path}: {e}')
                    continue
                written_arcs.add(arc)
                written += 1
                done += size
                now = time.time()
                if now - last_report > 0.25:
                    last_report = now
                    report(5 + 94.0 * done / total,
                           f'打包中 {written} 个文件 · 已处理 {_fmt_size(done)}')

    # 清理数据库导出临时目录
    if db_temp.exists():
        shutil.rmtree(db_temp, ignore_errors=True)

    size = zip_path.stat().st_size
    report(100, '完成')

    warnings = list(stats['warnings'])
    if duplicates:
        warnings.append(f'跳过 {len(duplicates)} 个重复条目（同名只保留一次）')

    result = {
        'kind': kind,
        'title': spec['title'],
        'package_name': pkg,
        'package_path': str(zip_path),
        'package_size': size,
        'file_count': written,
        'skipped': skipped[:50],
        'duplicates': duplicates[:20],
        'warnings': warnings,
        'stats': stats,
        'created': created.strftime('%Y-%m-%d %H:%M:%S'),
    }
    # 同时落一份清单在 ZIP 旁边，方便不打开压缩包就能核对
    try:
        (Path(S.BACKUP_ROOT) / f'{pkg}.manifest.json').write_text(
            json.dumps({
                'kind': kind, 'package': str(zip_path), 'size': size,
                'file_count': written, 'created': result['created'],
                'scope_includes': spec['includes'], 'scope_excludes': spec['excludes'],
                'skipped': result['skipped'], 'duplicates': result['duplicates'],
                'warnings': warnings,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass
    return result


def _zhi_shi_readme():
    return (
        '# 知识库目录（ZhiShi）\n\n'
        '本目录存放平台的全部知识内容，与平台程序（XiTong）同级。\n\n'
        '- `02_运行档案库`：系统搭建文档与操作流水\n'
        '- `03_知识库`：本地导入 / 网页解析 / 视频转图文 / 文本输入 / AI 生成 / 书籍 /\n'
        '  灵感库 / WIKI / 自媒体 / 办公平台 / 工作文件\n'
        '- `04_临时中转`：导入过程中的临时文件\n'
        '- `05_归档库`：归档内容\n\n'
        '平台通过配置文件自动定位本目录：默认取平台程序所在目录的同级 `ZhiShi`，\n'
        '也可用环境变量 `ZHISHIKU_DATA_ROOT` 指定到别处。\n'
    )
