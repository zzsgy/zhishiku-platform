"""系统设置：系统状态（平台/知识库架构）· Gitee 库状态 · 工作台设置 · AI 多服务 · 备份与迁移。"""
import os
import json
import time
import threading

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.http import JsonResponse, FileResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.models import SystemConfig, KnowledgeBase, KnowledgeNode, OperationLog, CollectionItem
from core.services import log_operation
from core import ai as ai_service

from .models import BackupJob
from . import backup_scopes as bk_scopes
from .backup_engine import scan as bk_scan, build as bk_build


PLATFORM_ARCH = [
    {'title': '总体架构', 'desc': 'Django 5.2 单体 + SQLite + CDN 前端（Bootstrap5/ECharts/D3/marked）', 'items': [
        '数据层：SQLite 关系库 + C:\\ZSK\\ZhiShi 文件系统（Markdown 落盘）',
        '服务层：core.services / core.parsers / core.ai 统一封装',
        '接入层：左侧固定竖排导航，11 个模块统一入口',
    ]},
    {'title': '知识层', 'desc': '知识库 / WiKI 层 / 知识星图 / 书架', 'items': [
        '知识库：运行档案库·知识库 两库分离',
        'WiKI 层：Markdown + [[双链]] + 反向链接（Obsidian 式）',
        '知识星图：D3 力导向图展示节点关系',
        '书架：分类·进度·笔记·划句问AI·金句库',
    ]},
    {'title': '采集层', 'desc': '知识收集四管道', 'items': [
        '本地导入：Word/PDF/MD/TXT/PPT/图片',
        '网页解析：普通网页/公众号/共享链接/B站图文',
        '视频转图文：抖音/优酷/爱奇艺/B站（Phase 8 补齐）',
        '文本输入：直接粘贴整理',
    ]},
    {'title': '应用层', 'desc': '总览 / 灵感库 / 办公平台 / 运行档案', 'items': [
        '总览：数据看板 + 趋势图 + 快捷入口',
        '灵感库：随身小记，任意页划句收入',
        '办公平台：行政/党建/运维 + 全网检索 + 风格化献策 + 报告',
        '运行档案：操作日志自动采集',
    ]},
]

MODULE_FUNCS = {
    '总览': '数据概览、近 7 天趋势、近期动态、各模块快捷入口',
    '知识星图': '全部知识节点的关系网络，拖拽/缩放/点击查看',
    'WiKI层': '自生长知识，Markdown 编辑与 [[双链]] 反向链接',
    '书架': '书籍收集、分类、阅读进度、笔记、金句库',
    '知识库': '运行档案库/知识库 两库分离总览与浏览',
    '灵感库': '随身小记，阅读页划句一键收入',
    '自媒体': '第 7 项模块占位（规划中）',
    '办公平台': '行政/党建/运维工作台、全网检索、风格化献策、日报周报月报年报',
    '知识收集': '本地导入/网页解析/视频转图文/文本输入 四条管道',
    '运行档案': '工作台全部操作流水（自动采集）',
    '系统设置': '系统状态、Gitee 库状态、工作台设置',
}


def _arch_children():
    """平台架构 → 树状子节点（层 → 条目）。"""
    return [
        {'name': a['title'], 'desc': a['desc'], 'type': 'layer',
         'children': [{'name': it, 'type': 'leaf'} for it in a['items']]}
        for a in PLATFORM_ARCH
    ]


def _module_children():
    """各功能模块说明 → 树状子节点。"""
    return [{'name': m, 'desc': f, 'type': 'leaf'} for m, f in MODULE_FUNCS.items()]


def _count_nodes(nodes):
    n = 0
    for x in nodes:
        n += 1 + _count_nodes(x.get('children') or [])
    return n


# 这些目录体量巨大且与平台运行无关，扫描目录树时跳过，避免页面卡顿/超时
_TREE_EXCLUDE = {
    'venv', 'static_collected', 'bin', '__pycache__', '.git', 'node_modules',
    '.idea', '.vscode', 'logs', 'media', 'db.sqlite3',
}

def _build_tree(root, max_depth=3):
    root = str(root)
    if not os.path.isdir(root):
        return []
    tree = []

    def walk(path, depth):
        if depth > max_depth:
            return []
        items = []
        try:
            for name in sorted(os.listdir(path)):
                if name in _TREE_EXCLUDE:
                    continue
                full = os.path.join(path, name)
                try:
                    if os.path.isdir(full):
                        items.append({'name': name, 'type': 'dir', 'children': walk(full, depth + 1)})
                    else:
                        items.append({'name': name, 'type': 'file'})
                except Exception:
                    # 个别文件无权限读取，跳过该节点，不影响整棵树
                    continue
        except Exception:
            pass
        return items

    return walk(root, 1)


def index(request):
    zhi_shi_tree = _build_tree(settings.ZHI_SHI_ROOT)
    xi_tong_tree = _build_tree(settings.BASE_DIR)

    # 旧版 dashscope_api_key 迁移进 qwen provider（仅首次）
    ai_service.migrate_legacy_key()
    providers = ai_service.ai_status()

    gitee_token = SystemConfig.get_value('gitee_token', '').strip()
    gitee_repos = []
    gitee_error = ''
    if gitee_token:
        try:
            import requests
            r = requests.get('https://gitee.com/api/v5/user/repos',
                             params={'access_token': gitee_token, 'per_page': 30}, timeout=15)
            if r.status_code == 200:
                gitee_repos = [{'name': x.get('name'), 'desc': x.get('description') or '',
                                'url': x.get('html_url'), 'lang': x.get('language')}
                               for x in r.json()]
            else:
                gitee_error = f'Gitee 返回状态码 {r.status_code}'
        except Exception as e:
            gitee_error = f'请求失败：{e}'
    else:
        gitee_error = '尚未配置 Gitee Token（在下方工作台设置填写）'

    stats = {
        'base': KnowledgeBase.objects.count(),
        'node': KnowledgeNode.objects.count(),
        'collection': CollectionItem.objects.count(),
        'log': OperationLog.objects.count(),
    }

    cfg = {
        'platform_name': SystemConfig.get_value('platform_name', '智识库'),
        'theme': SystemConfig.get_value('theme', 'light'),
        'user_style': SystemConfig.get_value('user_style', ''),
        'user_profile': SystemConfig.get_value('user_profile', ''),
        'user_taboo': SystemConfig.get_value('user_taboo', ''),
        'gitee_token': gitee_token,
    }

    # 四板块树状总览：平台架构 / 功能模块 / 知识库目录 / 系统目录
    # default_depth：初始展开到的层级（该层以内默认折叠，用户可手动展开）
    arch_sections = [
        {'key': 'arch', 'title': '系统状态 · 平台架构', 'icon': 'bi-diagram-3',
         'tone': 'primary', 'default_depth': 3, 'count': _count_nodes(_arch_children())},
        {'key': 'modules', 'title': '各功能模块说明', 'icon': 'bi-grid-1x2',
         'tone': 'success', 'default_depth': 1, 'count': _count_nodes(_module_children())},
        {'key': 'zhishi', 'title': '知识库目录', 'icon': 'bi-folder2-open',
         'tone': 'warning', 'default_depth': 2, 'root': str(settings.ZHI_SHI_ROOT),
         'count': _count_nodes(zhi_shi_tree)},
        {'key': 'xitong', 'title': '系统目录', 'icon': 'bi-folder2',
         'tone': 'info', 'default_depth': 2, 'root': str(settings.BASE_DIR),
         'count': _count_nodes(xi_tong_tree)},
    ]
    arch_tree = {
        'arch': _arch_children(),
        'modules': _module_children(),
        'zhishi': zhi_shi_tree,
        'xitong': xi_tong_tree,
    }

    return render(request, 'settings.html', {
        'arch_sections': arch_sections,
        'arch_tree': arch_tree,
        'gitee_repos': gitee_repos,
        'gitee_error': gitee_error,
        'gitee_token_configured': bool(gitee_token),
        'stats': stats,
        'cfg': cfg,
        'providers': providers,
        'provider_types': ai_service.PROVIDER_TYPES,
        # 备份与迁移：范围定义（静态，随手可得）+ 历史任务（含失败/失效状态）
        'backup_specs': backup_specs_payload(),
        'backup_jobs': [j.to_dict() for j in BackupJob.objects.all()[:8]],
        'backup_root': str(settings.BACKUP_ROOT),
        'backup_max_size': settings.BACKUP_MAX_BYTES,
    })


def save_settings(request):
    if request.method != 'POST':
        return redirect('/settings/')
    # 按字段存在与否分别更新：设置页有多个表单，避免某一表单提交时把别的键清空
    if 'platform_name' in request.POST:
        SystemConfig.set_value('platform_name', (request.POST.get('platform_name') or '智识库').strip())
    if 'theme' in request.POST:
        SystemConfig.set_value('theme', request.POST.get('theme', 'light'))
    if 'gitee_token' in request.POST:
        # 注：dashscope_api_key 已废弃（已迁移进 qwen provider），不再死写空串
        SystemConfig.set_value('gitee_token', (request.POST.get('gitee_token') or '').strip())
    if 'user_style' in request.POST:
        SystemConfig.set_value('user_style', (request.POST.get('user_style') or '').strip())
    if 'user_profile' in request.POST:
        SystemConfig.set_value('user_profile', (request.POST.get('user_profile') or '').strip())
    if 'user_taboo' in request.POST:
        SystemConfig.set_value('user_taboo', (request.POST.get('user_taboo') or '').strip())
    log_operation('settings', 'save')
    if 'user_profile' in request.POST or 'user_taboo' in request.POST:
        return redirect('/settings/?saved=1#persona')
    return redirect('/settings/?saved=1')


@csrf_exempt
def gitee_repos_api(request):
    """非阻塞获取 Gitee 仓库列表（带 5 分钟缓存，避免设置页每次打开都同步请求阻塞整页）。"""
    token = SystemConfig.get_value('gitee_token', '').strip()
    if not token:
        return JsonResponse({'ok': False, 'error': '尚未配置 Gitee Token（在下方工作台设置填写）'}, status=200)
    cache = SystemConfig.get_value('gitee_repos_cache', '')
    ts = SystemConfig.get_value('gitee_repos_cache_ts', '0')
    now = time.time()
    try:
        if cache and now - float(ts) < 300:
            return JsonResponse({'ok': True, 'repos': json.loads(cache), 'cached': True})
    except Exception:
        pass
    try:
        import requests
        r = requests.get('https://gitee.com/api/v5/user/repos',
                         params={'access_token': token, 'per_page': 30}, timeout=10)
        if r.status_code == 200:
            repos = [{'name': x.get('name'), 'desc': x.get('description') or '',
                      'url': x.get('html_url'), 'lang': x.get('language')} for x in r.json()]
            SystemConfig.set_value('gitee_repos_cache', json.dumps(repos, ensure_ascii=False))
            SystemConfig.set_value('gitee_repos_cache_ts', str(now))
            return JsonResponse({'ok': True, 'repos': repos, 'cached': False})
        return JsonResponse({'ok': False, 'error': f'Gitee 返回状态码 {r.status_code}'}, status=200)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'请求失败：{e}'}, status=200)


# ----------------------------- AI 多服务配置 -----------------------------
def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except Exception:
        return {}


def api_providers(request):
    """供前端（如划句问 AI 下拉）获取已启用服务列表。"""
    data = [{
        'id': p.get('id'), 'name': p.get('name'),
        'configured': bool((p.get('api_key') or '').strip()),
    } for p in ai_service.get_providers() if p.get('enabled')]
    return JsonResponse({'ok': True, 'providers': data})


def ai_save(request):
    if request.method != 'POST':
        return redirect('/settings/')
    data = _json_body(request) if request.content_type == 'application/json' else request.POST
    name = (data.get('name') or '').strip()
    if not name:
        if request.content_type == 'application/json':
            return JsonResponse({'ok': False, 'error': '服务名称不能为空'}, status=400)
        return redirect('/settings/')
    pid = ai_service.upsert_provider({
        'id': (data.get('id') or '').strip(),
        'name': name,
        'type': data.get('type', 'openai'),
        'api_key': data.get('api_key'),
        'model': data.get('model'),
        'base_url': data.get('base_url'),
        'enabled': data.get('enabled', False) in (True, 'true', 'on', '1'),
        'is_default': data.get('is_default', False) in (True, 'true', 'on', '1'),
    })
    if request.content_type == 'application/json':
        return JsonResponse({'ok': True, 'id': pid})
    return redirect('/settings/')


def ai_delete(request, pid):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    ai_service.delete_provider(pid)
    if request.content_type == 'application/json':
        return JsonResponse({'ok': True})
    return redirect('/settings/')


def ai_default(request, pid):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    ai_service.set_default(pid)
    if request.content_type == 'application/json':
        return JsonResponse({'ok': True})
    return redirect('/settings/')


def ai_test(request, pid):
    ok, msg = ai_service.test_provider(pid)
    return JsonResponse({'ok': ok, 'message': msg})


def ai_ask_all(request):
    """并行询问所有已启用服务，返回各服务回答（演示「并行接入」）。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    data = _json_body(request)
    system = (data.get('system') or '你是一个简洁的中文助手。').strip()
    user = (data.get('user') or '用一句话介绍你自己。').strip()
    results = ai_service.ask_all(system, user)
    return JsonResponse({'ok': True, 'results': results})


# ===========================================================================
# 备份与迁移
#
# 三个诉求 -> 三个 kind（范围定义见 backup_scopes.py，唯一事实源）：
#   knowledge 知识备份：只含知识资料，不含程序
#   system    系统备份：只含程序与依赖，零知识数据，可发给他人一键装空平台
#   migration 迁移备份：两者并集，换机整体搬迁
#
# 打包在后台线程里跑（大包要几分钟），进度写入 BackupJob 供前端轮询；
# 本模块只做编排，不重复定义范围、不自己拼路径。
# ===========================================================================
def backup_specs_payload():
    """把三类范围定义转成前端可直接渲染的结构（含可选项）。"""
    payload = []
    for s in bk_scopes.all_specs():
        payload.append({
            'kind': s['kind'],
            'title': s['title'],
            'subtitle': s['subtitle'],
            'icon': s['icon'],
            'tone': s['tone'],
            'badge': s['badge'],
            'purpose': s['purpose'],
            'includes': s['includes'],
            'excludes': s['excludes'],
            'restore_hint': s['restore_hint'],
            'options': s.get('options', []),
        })
    return payload


def _backup_options(kind, data):
    """从请求里解析可选项，非法值一律回落到默认值。"""
    raw = data.get('options')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return bk_scopes.merge_options(kind, raw)


def _touch(job_id, **fields):
    """更新任务进度；失败不抛异常（进度只是观感，不能反过来打断打包）。"""
    try:
        BackupJob.objects.filter(pk=job_id).update(**fields)
    except Exception:
        pass


def _run_backup_job(job_id, kind, options):
    """后台线程主体：跑一次打包并把结果写回任务记录。"""
    from django.db import connections

    spec = bk_scopes.scope_spec(kind)
    _touch(job_id, status='running', progress=1, stage='开始打包')

    def on_progress(percent, stage):
        _touch(job_id, progress=max(0, min(100, int(percent))), stage=(stage or '')[:80])

    try:
        result = bk_build(kind, on_progress=on_progress, options=options)
        notes = []
        if result['warnings']:
            notes.append('告警：' + '；'.join(result['warnings']))
        if result['skipped']:
            notes.append(f'跳过 {len(result["skipped"])} 个无法读取的文件')
        _touch(
            job_id,
            status='done', progress=100, stage='已完成',
            message='\n'.join(notes),
            package_path=result['package_path'],
            package_name=result['package_name'],
            package_size=result['package_size'],
            file_count=result['file_count'],
            finished=timezone.now(),
        )
        log_operation('settings', 'backup_' + kind,
                      detail=f'{result["package_name"]} · {result["package_size"]} 字节')
    except Exception as e:  # 打包失败要如实说明，不能留一个永远 99% 的任务
        _touch(job_id, status='failed', stage='失败',
               message=str(e), finished=timezone.now())
        log_operation('settings', 'backup_' + kind + '_failed', detail=str(e))
    finally:
        # 后台线程持有的连接必须显式关掉，否则 SQLite 会留下悬空连接
        try:
            connections.close_all()
        except Exception:
            pass


def backup_preview(request):
    """只读预演：本次会打包多少文件、多大体积、逐项明细。"""
    kind = (request.GET.get('kind') or '').strip()
    if kind not in bk_scopes.SCOPES:
        return JsonResponse({'ok': False, 'error': '未知的备份类型'}, status=400)
    try:
        data = bk_scan(kind, _backup_options(kind, request.GET.dict()))
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'范围预演失败：{e}'}, status=200)
    spec = bk_scopes.scope_spec(kind)
    data['ok'] = True
    data['title'] = spec['title']
    data['db_dir'] = 'database/'
    data['backup_root'] = str(settings.BACKUP_ROOT)
    return JsonResponse(data)


def backup_start(request):
    """启动一次备份。同一时刻只允许一个任务，避免两条线程抢磁盘与数据库。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    data = _json_body(request) if request.content_type == 'application/json' else request.POST
    kind = (data.get('kind') or '').strip()
    if kind not in bk_scopes.SCOPES:
        return JsonResponse({'ok': False, 'error': '未知的备份类型'}, status=400)
    if BackupJob.objects.filter(status__in=('pending', 'running')).exists():
        return JsonResponse({'ok': False, 'error': '已有备份任务正在执行，请等它结束后再试。'})

    options = _backup_options(kind, data)
    spec = bk_scopes.scope_spec(kind)
    job = BackupJob.objects.create(
        kind=kind,
        options_json=json.dumps(options, ensure_ascii=False),
        scope_json=json.dumps({'includes': spec['includes'],
                               'excludes': spec['excludes']}, ensure_ascii=False),
    )
    threading.Thread(target=_run_backup_job, args=(job.pk, kind, options),
                     name=f'zhishiku-backup-{job.pk}', daemon=True).start()
    return JsonResponse({'ok': True, 'id': job.pk})


def backup_status(request, pk):
    """轮询单个任务进度。"""
    job = get_object_or_404(BackupJob, pk=pk)
    return JsonResponse({'ok': True, 'job': job.to_dict(),
                         'root': str(settings.BACKUP_ROOT)})


def backup_list(request):
    """历史备份列表（页面刷新后仍可核对）。"""
    jobs = [j.to_dict() for j in BackupJob.objects.all()[:20]]
    return JsonResponse({'ok': True, 'jobs': jobs, 'root': str(settings.BACKUP_ROOT)})


def backup_download(request, pk):
    """下载备份包。"""
    job = get_object_or_404(BackupJob, pk=pk)
    if not job.package_exists:
        return JsonResponse({'ok': False, 'error': '备份包已不在磁盘上（可能已被移动或删除）'},
                            status=404)
    try:
        handle = open(job.package_path, 'rb')
    except OSError as e:
        return JsonResponse({'ok': False, 'error': f'无法打开备份包：{e}'}, status=500)
    resp = FileResponse(handle, as_attachment=True, filename=job.package_filename)
    resp['Content-Type'] = 'application/zip'
    return resp


def _inside_backup_root(path):
    """只允许删除备份输出目录内的文件——防止路径被篡改后误删别处。"""
    try:
        root = os.path.realpath(str(settings.BACKUP_ROOT))
        target = os.path.realpath(str(path))
        return os.path.commonpath([root, target]) == root
    except (ValueError, OSError):
        return False


def backup_delete(request, pk):
    """删除一次备份记录及其产物（连同旁边的 .manifest.json）。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    job = get_object_or_404(BackupJob, pk=pk)
    if job.is_active:
        return JsonResponse({'ok': False, 'error': '任务进行中，无法删除。'})

    removed, refused = [], []
    candidates = []
    if job.package_path:
        candidates.append(job.package_path)
        candidates.append(os.path.splitext(job.package_path)[0] + '.manifest.json')
    for p in candidates:
        if not p or not os.path.exists(p):
            continue
        if not _inside_backup_root(p):
            refused.append(p)
            continue
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            refused.append(f'{os.path.basename(p)}（{e}）')

    job.delete()
    log_operation('settings', 'backup_delete',
                  detail='删除 ' + (', '.join(removed) or '（无产物）'))
    return JsonResponse({'ok': True, 'removed': removed, 'refused': refused})


def backup_reveal(request):
    """在资源管理器中打开备份输出目录（让用户直接看到/拷走备份包）。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    path = str(settings.BACKUP_ROOT)
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)  # noqa: S606 - Windows 专用，本平台只在 Windows 上跑
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'无法打开文件夹：{e}'})
    return JsonResponse({'ok': True, 'path': path})
