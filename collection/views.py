"""知识收集：本地导入 / 网页解析 / 视频转图文 / 图片上传（含截图粘贴）。

所有解析结果统一以 Markdown 形式落入「知识库」对应子目录，并生成知识节点。
视频转图文依赖 ffmpeg/whisper，Phase 8 补齐，此处先受理并记录任务。
文本随手记已收敛至「随笔记」模块，此处不再提供文本输入。
"""
import os
import re
import json
from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import quote

from core.models import CollectionItem, KnowledgeBase, LinkItem
from core.services import (
    log_operation, default_base, link_collection_to_node, add_link_to_library,
    sanitize_filename,
)
from core.parsers import parse_local_file, parse_web_page
from core.workfolder import (
    SUPPORTED_EXTS, WORKFILE_CATEGORIES, WORKFILE_CATEGORY_LABELS,
    workfile_dir, index_uploaded_file,
)
import logging
from django.utils import timezone

logger = logging.getLogger('kb')


# ---------------------------------------------------------------------------
# 「最近收集」实时状态：所有导入入口统一支持 AJAX，返回结构化状态
# ---------------------------------------------------------------------------
def _wants_json(request):
    """判断是否为页面 AJAX（fetch/XHR）提交。

    是：返回 JSON 结构化状态，由知识收集页实时更新「最近收集」行；
    否：维持原有「跳转 + messages」行为，保证禁用 JS 时功能不退化。
    """
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return str(request.POST.get('_ajax') or '').strip() == '1'


def _json_state(ok, status, message, kind='', title='', reject=False, item_id=None,
                source_url=None):
    """统一的导入状态响应体：status ∈ pending / done / failed。

    item_id：本次导入对应的收集记录主键，前端据此把「进行中」临时行与
    服务端记录对齐（刷新 / 轮询时不会出现两行）。
    """
    data = {'ok': bool(ok), 'status': status, 'message': message or '',
            'kind': kind, 'title': title or ''}
    if reject:
        # reject：请求未进入处理（如未选文件），前端撤销临时行、不落记录
        data['reject'] = True
    if item_id:
        data['id'] = item_id
    if source_url:
        data['source_url'] = source_url
    return JsonResponse(data)


def _start_item(kind, title, base=None, url=''):
    """受理导入：先落一条「进行中」记录。

    先落库的好处：导入期间刷新页面，该任务仍以「进行中」留在「最近收集」里，
    不会因为页面重载而丢失；处理结束再收口为 done / failed。
    """
    try:
        return CollectionItem.objects.create(
            kind=kind, title=(title or '未命名')[:250], status='pending',
            base=base, source_url=url or '', note='正在处理…')
    except Exception as e:
        logger.error('创建收集记录失败: %s', e)
        return None


def _settle_item(item, status, note, title=None, source_url=None, md=None):
    """收口导入结果：写入最终状态与结果说明。"""
    if not item:
        return
    try:
        item.status = status
        item.note = note or ''
        if title:
            item.title = title[:250]
        if source_url is not None:
            item.source_url = source_url
        if md is not None:
            item.parsed_md = md
            item.raw_text = md[:2000]
        item.save()
    except Exception as e:
        logger.error('更新收集状态失败: %s', e)


def _reply_state(request, *, ok, status, message, kind='', title='', level='success',
                 reject=False, item_id=None, source_url=None):
    """AJAX → JSON 状态；普通表单 → messages + 跳转（原行为不变）。"""
    if _wants_json(request):
        return _json_state(ok, status, message, kind=kind, title=title, reject=reject,
                           item_id=item_id, source_url=source_url)
    getattr(messages, level)(request, message)
    return redirect('/collection/')


def recent_items(request):
    """「最近收集」状态快照（轻量 JSON），供页面轮询收敛「进行中」的行。

    页面在以下场景依赖它：导入期间刷新 / 换了标签页，此时页面上那条「进行中」
    由服务端的 pending 记录渲染而来，靠本接口轮询到最终状态后原地更新。
    """
    labels = dict(CollectionItem.KIND)
    data = []
    for it in CollectionItem.objects.all()[:20]:
        data.append({
            'id': it.pk,
            'kind': it.kind,
            'kind_label': labels.get(it.kind, it.kind),
            'title': it.title or '未命名',
            'status': it.status,
            'note': it.note or '',
            'source_url': it.source_url or '',
            'created': timezone.localtime(it.created).strftime('%m-%d %H:%M'),
        })
    return JsonResponse({'ok': True, 'items': data})


def _fail_reason_from_exc(e):
    """根据解析异常推断『无法解析原因』，用于链接库归档。

    优先采用抓取侧给出的 ``reason``（``core.article_parser.WebParseError``）：
    它是在现场判定的（SPA 空壳 / 登录墙 / 付费墙 / HTTPS 状态码），比事后按
    异常类型猜要准；只有拿不到时才退回下面的启发式。
    """
    valid = {key for key, _label in LinkItem.REASON_CHOICES}
    reason = getattr(e, 'reason', '')
    if reason in valid:
        return reason
    try:
        import requests
    except Exception:
        requests = None
    if requests and isinstance(e, requests.exceptions.Timeout):
        return 'timeout'
    if requests and isinstance(e, requests.exceptions.ConnectionError):
        return 'antibot'
    msg = str(e).lower()
    if '403' in msg or '429' in msg or 'forbidden' in msg or 'too many' in msg or 'blocked' in msg:
        return 'antibot'
    if '404' in msg or '410' in msg:
        return 'invalid'
    if 'paywall' in msg or '402' in msg or 'subscription' in msg:
        return 'paywall'
    return 'unknown'


def record_failed_link(url, source, reason, link_type='unknown', title=''):
    """解析失败时把链接归档到链接库，去重并更新重试计数。"""
    if not url:
        return
    try:
        item, created = LinkItem.objects.get_or_create(
            url=url,
            defaults={'source': source, 'unparse_reason': reason,
                      'link_type': link_type, 'status': 'pending',
                      'title': title, 'collector': '系统'})
        if not created:
            item.retry_count = (item.retry_count or 0) + 1
            item.last_checked = timezone.now()
            item.save(update_fields=['retry_count', 'last_checked'])
        log_operation('link_library', 'auto_archive', detail=url)
    except Exception as e:
        logger.error('record_failed_link failed: %s', e)


def index(request):
    items = CollectionItem.objects.all()[:40]
    kind_labels = dict(CollectionItem.KIND)
    return render(request, 'collection.html', {
        'items': items,
        'kind_labels': kind_labels,
        # 链接录入分类项：原链接库「手动添加」的分类下拉迁移至此统一管理与录入，
        # 数据字典以 LinkItem.TYPE_CHOICES 为唯一来源（来源由服务端自动确定，不暴露）
        'link_type_choices': LinkItem.TYPE_CHOICES,
        # 「存入办公平台」的工作分类（行政管理 / 基层党建 / 网络运维）
        'office_categories': WORKFILE_CATEGORIES,
    })


@csrf_exempt
def input_text(request):
    """随身记「保存知识库」入口：把一条文本快速落成知识节点。

    对应此前在重构中被移除的 collection 文本输入能力；前端 notes.html
    的「保存知识库」按钮仍 POST 至此路径，此处予以恢复，保持模块协调一致。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    text = (request.POST.get('text') or '').strip()
    title = (request.POST.get('title') or '').strip() or '随笔记'
    if not text:
        return JsonResponse({'ok': False, 'error': '内容为空'}, status=400)
    base = default_base('knowledge')
    if not base:
        return JsonResponse({'ok': False, 'error': '未找到知识库'}, status=400)
    from core.models import CollectionItem
    item = CollectionItem.objects.create(
        kind='text', title=title, raw_text=text, parsed_md=text,
        status='done', base=base)
    try:
        node = link_collection_to_node(item, base=base, title=title, category='文本输入')
        log_operation('collection', 'input_text', detail=title)
        return JsonResponse({'ok': True, 'id': node.pk})
    except Exception as e:
        logger.error('input_text link failed: %s', e)
        return JsonResponse({'ok': False, 'error': str(e)[:120]}, status=400)


def _pick_base(base_id):
    if base_id:
        b = KnowledgeBase.objects.filter(pk=base_id).first()
        if b:
            return b
    return default_base('knowledge')


def import_local(request):
    """本地导入：解析文件并写入知识库。

    支持 AJAX：受理时先落一条「进行中」记录，页面上「最近收集」立即出现该行；
    处理结束再收口为「已完成 / 失败」，失败同样留痕并附原因，用户不必离开本页。
    """
    if request.method != 'POST':
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='仅支持 POST 提交', reject=True)
    f = request.FILES.get('file')
    title = (request.POST.get('title') or '').strip()
    base = _pick_base(request.POST.get('base'))
    if not f:
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='请先选择要导入的文件。', reject=True)

    display = title or f.name
    item = _start_item('local', display, base=base)

    upload_dir = settings.MEDIA_ROOT / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f.name
    with open(dest, 'wb') as out:
        for chunk in f.chunks():
            out.write(chunk)

    try:
        name, md, meta = parse_local_file(
            f, f.name, upload_url=f'/media/uploads/{quote(f.name)}')
    except Exception as e:
        msg = f'解析失败：{e}'
        _settle_item(item, 'failed', msg)
        log_operation('collection', 'import_local', detail=f'{display}（失败）')
        return _reply_state(request, ok=False, status='failed', level='error',
                            message=msg, kind='local', title=display,
                            item_id=(item.pk if item else None))

    # 先落解析内容，再落知识节点（link_collection_to_node 取 item.parsed_md 作为正文）
    item.parsed_md = md
    item.raw_text = md[:2000]
    item.save(update_fields=['parsed_md', 'raw_text'])

    final_title, level = (title or name), 'success'
    try:
        node = link_collection_to_node(item, base=base, title=title or name, category='本地导入')
        final_title = node.title
        msg = f'已导入并写入知识库：{node.title}（{meta.get("ext", "")}）'
    except Exception as e:
        level = 'warning'
        msg = f'已解析但未落库：{e}'
    _settle_item(item, 'done', msg, title=final_title)
    log_operation('collection', 'import_local', detail=title or name)
    return _reply_state(request, ok=True, status='done', level=level,
                        message=msg, kind='local', title=final_title,
                        item_id=(item.pk if item else None))


def import_office(request):
    """「存入办公平台」：解析所选文件并按工作分类归档进办公平台的「工作文件目录」。

    与「导入并解析」的区别：本入口**不写入知识库**，而是把文件副本存到
    03_知识库/11_工作文件/<分类>/ 并登记 WorkFileIndex，供办公平台按分类展示与只读调阅。
    分类取 行政管理 / 基层党建 / 网络运维；不触碰任何原始文件。
    同样支持 AJAX：在「最近收集」实时展示 进行中 / 已完成 / 失败。
    """
    if request.method != 'POST':
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='仅支持 POST 提交', reject=True)
    f = request.FILES.get('file')
    title = (request.POST.get('title') or '').strip()
    cat_key = (request.POST.get('office_cat') or '').strip()
    cat_label = WORKFILE_CATEGORY_LABELS.get(cat_key)
    if not f:
        return _reply_state(request, ok=False, status='failed', level='error', reject=True,
                            message='请先在「本地导入」选择要存入办公平台的文件。')
    if not cat_label:
        return _reply_state(request, ok=False, status='failed', level='error', reject=True,
                            message='请选择工作分类（行政管理 / 基层党建 / 网络运维）。')

    stem, ext_with_dot = os.path.splitext(f.name or '')
    ext = (ext_with_dot or '').lstrip('.').lower()
    if ext not in SUPPORTED_EXTS:
        return _reply_state(request, ok=False, status='failed', level='error', reject=True,
                            message='不支持的文件格式：%s（支持 Word / PDF / Markdown / TXT / PPT / 图片）'
                                    % (ext or '未知'))

    display = title or stem or f.name
    item = _start_item('office', display)
    if item:
        item.note = '正在存入办公平台「%s」…' % cat_label
        item.save(update_fields=['note'])

    target_dir = workfile_dir(cat_label)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timezone.now().strftime('%Y%m%d%H%M%S')
    dest = target_dir / ('%s_%s%s' % (stamp, sanitize_filename(stem), ext_with_dot or '.%s' % ext))

    # 1) 先落盘副本（与上传流解耦，保证字节完整）
    try:
        with open(dest, 'wb') as out:
            for chunk in f.chunks():
                out.write(chunk)
    except Exception as e:
        msg = '保存失败：%s' % e
        _settle_item(item, 'failed', msg)
        return _reply_state(request, ok=False, status='failed', level='error',
                            message=msg, kind='office', title=display,
                            item_id=(item.pk if item else None))

    # 2) 从落盘文件解析；失败也保留副本并登记，至少可在办公平台打开原件
    parsed_title, md, warn = (stem or '未命名'), '', ''
    try:
        with open(dest, 'rb') as fh:
            parsed_title, md, _meta = parse_local_file(fh, f.name)
    except Exception as e:
        warn = '（内容解析跳过：%s）' % str(e)[:60]

    # 3) 登记到办公平台「工作文件目录」的对应分类
    try:
        entry = index_uploaded_file(dest, title=title or parsed_title or stem,
                                    category=cat_label, extracted=md or '')
        log_operation('office', 'workfile_in', detail='%s/%s' % (cat_label, entry.title))
        msg = '已存入办公平台「%s」：%s%s' % (cat_label, entry.title, warn)
        _settle_item(item, 'done', msg, title=entry.title, source_url=str(dest))
        return _reply_state(request, ok=True, status='done', message=msg,
                            kind='office', title=entry.title,
                            item_id=(item.pk if item else None), source_url=str(dest))
    except Exception as e:
        msg = '文件已保存但登记失败：%s' % e
        _settle_item(item, 'failed', msg, title=display, source_url=str(dest))
        return _reply_state(request, ok=False, status='failed', level='error',
                            message=msg, kind='office', title=display,
                            item_id=(item.pk if item else None), source_url=str(dest))


def parse_web(request):
    if request.method != 'POST':
        return redirect('/collection/')
    url = (request.POST.get('url') or '').strip()
    platform = (request.POST.get('platform') or 'auto').strip()
    title = (request.POST.get('title') or '').strip()
    base = _pick_base(request.POST.get('base'))
    if not url:
        messages.error(request, '请输入要解析的链接。')
        return redirect('/collection/')
    try:
        t, md = parse_web_page(url, platform)
    except Exception as e:
        record_failed_link(url, 'web', _fail_reason_from_exc(e))
        messages.error(request, f'网页解析失败：{e}（已归档至链接库）')
        return redirect('/collection/')
    item = CollectionItem.objects.create(
        kind='web', title=title or t, parsed_md=md, status='done', base=base, source_url=url)
    try:
        node = link_collection_to_node(item, base=base, title=title or t, category='网页解析')
        messages.success(request, f'已解析网页并写入知识库：{node.title}')
    except Exception as e:
        messages.warning(request, f'已解析但未落库：{e}')
    log_operation('collection', 'parse_web', detail=url)
    return redirect('/collection/')


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}


def _safe_image_name(orig_name):
    """为上传/粘贴的图片生成不重名且路径安全的文件名。"""
    stem, ext = os.path.splitext(orig_name or 'paste.png')
    stem = re.sub(r'[^\w\u4e00-\u9fff-]+', '_', stem).strip('_') or 'paste'
    ext = ext.lower() if ext.lower() in IMAGE_EXTS else '.png'
    ts = timezone.now().strftime('%Y%m%d%H%M%S')
    return f'{ts}_{stem}{ext}'


def import_image(request):
    """图片上传与截图粘贴：保存原图 → OCR（尽力）→ Markdown 入知识库。

    支持 AJAX：受理即落「进行中」记录，「最近收集」实时反映 OCR 进度与结果。
    """
    if request.method != 'POST':
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='仅支持 POST 提交', reject=True)
    f = request.FILES.get('image')
    title = (request.POST.get('title') or '').strip()
    base = _pick_base(request.POST.get('base'))
    if not f:
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='请先选择或粘贴图片。', reject=True)

    ext = os.path.splitext(f.name or '')[1].lower()
    if ext and ext not in IMAGE_EXTS:
        return _reply_state(request, ok=False, status='failed', level='error', reject=True,
                            message=f'仅支持图片格式（jpg/png/bmp/gif/webp），收到：{ext}')

    display = title or f.name or '粘贴截图'
    item = _start_item('image', display, base=base)

    upload_dir = settings.MEDIA_ROOT / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_image_name(f.name)
    dest = upload_dir / name
    with open(dest, 'wb') as out:
        for chunk in f.chunks():
            out.write(chunk)
    upload_url = f'/media/uploads/{name}'

    try:
        name2, md, meta = parse_local_file(f, name, upload_url=upload_url)
    except Exception as e:
        msg = f'图片处理失败：{e}'
        _settle_item(item, 'failed', msg, source_url=upload_url)
        log_operation('collection', 'import_image', detail=f'{display}（失败）')
        return _reply_state(request, ok=False, status='failed', level='error',
                            message=msg, kind='image', title=display,
                            item_id=(item.pk if item else None), source_url=upload_url)

    item.parsed_md = md
    item.raw_text = md[:2000]
    item.source_url = upload_url
    item.save(update_fields=['parsed_md', 'raw_text', 'source_url'])

    final_title, level = (title or name2), 'success'
    try:
        node = link_collection_to_node(item, base=base, title=title or name2, category='图片上传')
        final_title = node.title
        extra = '（含 OCR 文字）' if meta.get('ocr') == 'done' else ''
        msg = f'图片已入库：{node.title}{extra}'
    except Exception as e:
        level = 'warning'
        msg = f'已保存但未落库：{e}'
    _settle_item(item, 'done', msg, title=final_title, source_url=upload_url)
    log_operation('collection', 'import_image', detail=title or name)
    return _reply_state(request, ok=True, status='done', level=level,
                        message=msg, kind='image', title=final_title,
                        item_id=(item.pk if item else None), source_url=upload_url)


def _extract_transcript(md):
    """从视频转图文 Markdown 中抽取『语音 / 字幕转写』文本段。"""
    import re
    m = re.search(r'##\s*语音\s*/\s*字幕转写\s*(.*?)(?=\s*##\s*重点截图|\Z)', md, re.S)
    return (m.group(1) or '').strip()


def _enrich_video_with_ai(md, title, url):
    """在视频转图文 Markdown 末尾追加 AI 智能摘要（调用现有 AI 模块 core.ai.ask_ai）。

    无可用 AI 服务或调用失败时原样返回，保证离线 / 无 Key 场景仍可用。
    """
    from core.ai import ask_ai, ai_available
    if not ai_available():
        return md
    transcript = _extract_transcript(md)
    # 转写段为空或仅为占位提示时跳过
    if not transcript or transcript.startswith('['):
        return md
    prompt = (
        f'以下是视频《{title}》的语音转写文本（来源：{url}）。\n'
        '请整理为结构化笔记，严格使用如下 Markdown 结构，不要额外解释：\n'
        '## 核心摘要\n（2-3 句话概括视频主旨）\n'
        '## 要点提炼\n（分条，每条一个核心观点）\n'
        '## 金句摘录\n（挑选 1-3 句有信息量的原话）\n\n'
        f'转写文本：\n{transcript[:6000]}'
    )
    system = ('你是视频内容整理助手，擅长把口语化、冗余的转写文本提炼为清晰、'
              '准确、可检索的结构化笔记，使用中文。')
    ans = ask_ai(system, prompt)
    if ans and ans.strip():
        return md + '\n\n---\n\n## AI 智能摘要\n\n' + ans.strip() + '\n'
    return md


def parse_video(request):
    """视频转图文：下载→音频→whisper 转写→关键帧截图→AI 智能摘要，结果以 Markdown 入库。

    支持 AJAX：本管道耗时最长（下载 / 转写 / 摘要），受理即落「进行中」记录，
    「最近收集」会持续显示该行的已用时，处理结束自动转为成功 / 失败。
    """
    if request.method != 'POST':
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='仅支持 POST 提交', reject=True)
    url = (request.POST.get('url') or '').strip()
    if not url:
        return _reply_state(request, ok=False, status='failed', level='error',
                            message='请输入视频链接。', reject=True)
    from core import media
    base = _pick_base(request.POST.get('base'))
    item = _start_item('video', url, base=base, url=url)
    if item:
        item.note = '正在下载视频并转写（较慢，请勿关闭页面）…'
        item.save(update_fields=['note'])
    try:
        title, md = media.video_to_markdown(url, settings.MEDIA_ROOT / 'videos' / 'tmp')
        md = _enrich_video_with_ai(md, title, url)
        item.parsed_md = md
        item.raw_text = md[:2000]
        item.save(update_fields=['parsed_md', 'raw_text'])
        node = link_collection_to_node(item, base=base, title=title, category='视频转图文')
        msg = f'视频转图文完成：{node.title}（已含 AI 智能摘要）'
        _settle_item(item, 'done', msg, title=node.title, source_url=url)
        log_operation('collection', 'parse_video', detail=url)
        return _reply_state(request, ok=True, status='done', message=msg,
                            kind='video', title=node.title,
                            item_id=(item.pk if item else None), source_url=url)
    except Exception as e:
        msg = f'视频转图文失败：{e}（已归档至链接库）'
        _settle_item(item, 'failed', msg, title='视频转图文（失败）', source_url=url)
        record_failed_link(url, 'web', 'unknown', link_type='video')
        log_operation('collection', 'parse_video', detail=f'{url}（失败）')
        return _reply_state(request, ok=False, status='failed', level='error',
                            message=msg, kind='video', title='视频转图文（失败）',
                            item_id=(item.pk if item else None), source_url=url)


def _parse_body(request):
    """解析 JSON 或表单请求体，供 AJAX 端点复用。"""
    if request.content_type == 'application/json':
        try:
            return json.loads(request.body)
        except Exception:
            return {}
    return request.POST


def _infer_link_type(url):
    """按 URL 特征推断链接类型（「自动识别」用）。"""
    u = (url or '').lower()
    if 'mp.weixin.qq.com' in u or 'weixin.qq.com' in u:
        return 'wechat'
    if 'bilibili.com' in u or 'b23.tv' in u:
        return 'bilibili'
    if u.split('?')[0].rstrip('/').endswith('.pdf'):
        return 'pdf'
    return 'webpage'


@csrf_exempt
def collection_link_action(request):
    """知识收集：网页解析 / 链接库录入 合并端点（JSON）。

    前端「解析并入库」「录入链接库」两个按钮共用；两者涉及「录入链接库」
    的行为都走 core.services.add_link_to_library，保证存储逻辑唯一：
      - mode='parse'：先尝试解析网页；成功则落成知识节点返回 {ok, parsed:true}；
                      失败则自动归档到链接库返回 {ok, archived:true, reason}。
      - mode='link' ：跳过解析，直接把链接录入链接库返回 {ok, id}。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    data = _parse_body(request)
    url = (data.get('url') or '').strip()
    title = (data.get('title') or '').strip()
    mode = (data.get('mode') or 'parse').strip()
    if not url:
        return JsonResponse({'ok': False, 'error': '请输入链接'}, status=400)

    # 链接分类：来源由本入口性质自动确定（网页解析渠道），不暴露给用户；
    # 类型由用户选择，「auto/未知」时按 URL 智能推断
    source = 'web'
    link_type = (data.get('link_type') or 'auto').strip()
    if link_type not in dict(LinkItem.TYPE_CHOICES):
        link_type = 'auto'
    if link_type in ('auto', 'unknown'):
        link_type = _infer_link_type(url)

    if mode == 'link':
        try:
            # 用户直接录入（跳过解析）：默认即为「链接归档」
            item = add_link_to_library(url, title=title, source=source,
                                      link_type=link_type, status='archived')
        except ValueError as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        return JsonResponse({'ok': True, 'id': item.pk, 'mode': 'link', 'status': 'done',
                             'kind': 'web', 'title': item.title or title or url,
                             'message': '已录入链接库'})

    # mode == 'parse'：先尝试解析网页；平台由所选类型派生（公众号/B站专用解析）
    platform = {'wechat': 'wechat', 'bilibili': 'bilibili'}.get(link_type, 'auto')
    if (data.get('platform') or '').strip() in ('wechat', 'bilibili', 'share', 'auto'):
        platform = data['platform'].strip()  # 兼容旧调用显式传 platform
    base = _pick_base(data.get('base'))
    try:
        t, md = parse_web_page(url, platform)
    except Exception as e:
        reason = _fail_reason_from_exc(e)
        try:
            item = add_link_to_library(url, title=title, source=source,
                                      link_type=link_type, unparse_reason=reason,
                                      status='pending')
            return JsonResponse({'ok': True, 'archived': True, 'reason': reason, 'id': item.pk,
                                 'status': 'failed', 'kind': 'web', 'title': title or url,
                                 'message': f'解析失败，已自动录入链接库（原因：{reason}）'})
        except ValueError:
            # 链接已存在于链接库：视为已纳入，不报错
            return JsonResponse({'ok': True, 'archived': True, 'reason': reason,
                                 'duplicate': True, 'status': 'failed', 'kind': 'web',
                                 'title': title or url,
                                 'message': f'解析失败，链接已在链接库中（原因：{reason}）'})
    item = CollectionItem.objects.create(
        kind='web', title=title or t, parsed_md=md, status='done', base=base, source_url=url)
    try:
        node = link_collection_to_node(item, base=base, title=title or t, category='网页解析')
        msg = f'已解析并写入知识库：{node.title}'
        item.title = node.title
        item.note = msg
        item.save(update_fields=['title', 'note'])
        return JsonResponse({'ok': True, 'parsed': True, 'status': 'done', 'kind': 'web',
                             'title': node.title, 'message': msg, 'id': item.pk})
    except Exception as e:
        logger.error('collection parse link node failed: %s', e)
        msg = f'已解析但未落库：{e}'
        item.note = msg
        item.save(update_fields=['note'])
        return JsonResponse({'ok': True, 'parsed': True, 'status': 'done', 'kind': 'web',
                             'title': title or t, 'message': msg, 'id': item.pk,
                             'warn': str(e)[:120]})
