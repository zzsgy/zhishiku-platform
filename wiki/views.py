"""WiKI 层：自生长知识。Markdown 编辑 + [[双链]] + 反向链接（Obsidian 式）。

节点（KnowledgeNode）与知识库共用，这里是其编辑与生长的核心界面。
提供划句问 AI 的 API（/wiki/api/ask/）。
"""
import json
import logging
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger('kb')

from django.db.models import Q
from core.models import KnowledgeNode, KnowledgeBase, WorkFileIndex
from bookshelf.models import GoldenSentence
from core.services import (
    render_markdown, sync_node_to_file, log_operation, auto_link_edges,
)
from core.ai import ask_ai
from core.extract import (
    ORIGIN_TYPES, resolve_origin, get_source_title, get_source_content,
    extract_knowledge, grow_node, create_precip_node, adopt_node, discard_node,
)


def _safe_parent(raw, self_pk=None):
    """解析父节点 pk；排除空值、自身、以及会形成环的祖先链命中。"""
    if not raw:
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    if self_pk is not None and pid == self_pk:
        return None
    # 向上回溯 pid 的祖先链，若命中 self_pk 则形成环，拒绝
    cur = KnowledgeNode.objects.filter(pk=pid).first()
    seen = set()
    while cur and cur.parent_id is not None:
        if cur.parent_id == self_pk:
            return None
        if cur.pk in seen:
            break
        seen.add(cur.pk)
        cur = KnowledgeNode.objects.filter(pk=cur.parent_id).first()
    return pid


# ---------------------------------------------------------------------------
# 正文编辑：渲染通道 + 内容骤减熔断
# 编辑页现在是「所见即所得」的富文本编辑区，Markdown 只是持久化格式。
# 因此必须补两件事：①富文本与阅读页出自同一渲染器；②防止富文本往返把正文吃掉。
# ---------------------------------------------------------------------------
_MD_MARK_RE = re.compile(r'[#>*`_~\[\]()!|]')
_WS_RE = re.compile(r'\s+')


def md_weight(text):
    """正文「信息量」：剔掉 Markdown 标记与全部空白后的字符数。

    不能用 len(text)：用户把整段改成 `## 标题` 这种标记密集的短内容时，
    字面长度会误导判断。剔标记后计数才是「内容还剩多少」的近似量。
    """
    return len(_WS_RE.sub('', _MD_MARK_RE.sub('', text or '')))


def content_loss(old_md, new_md):
    """判断本次提交是否属于「内容骤减」。返回 (是否骤减, 旧量, 新量)。

    阈值取「新量 < 旧量 60% 且净减 > 80 字」：既能拦住富文本往返导致的
    大面积丢失，又不会误伤正常的删改段落。旧内容过短时直接放行。
    """
    old_w, new_w = md_weight(old_md), md_weight(new_md)
    if old_w < 40:
        return False, old_w, new_w
    return (new_w < old_w * 0.6 and (old_w - new_w) > 80), old_w, new_w


def _edit_context(node, request=None, form_md=None, loss_warning='', submitted=None):
    """构造编辑页上下文。

    form_md / submitted 用于「拦截后回显」：内容骤减被拦下时必须把用户刚写的内容
    原样送回表单，否则一次拦截会变成二次数据损失。node 为 None 表示新建。
    """
    sub = submitted if submitted is not None else {}

    def pick(name, default):
        return sub.get(name, default) if submitted is not None else default

    md = (node.content_md if node else '') if form_md is None else form_md
    parent_candidates = (KnowledgeNode.objects.exclude(pk=node.pk) if node
                         else KnowledgeNode.objects.all()).order_by('title')
    return {
        'node': node,
        'bases': None if node else KnowledgeBase.objects.all(),
        'parent_candidates': parent_candidates,
        'form_md': md,
        # 富文本区直接吃这条 HTML：与阅读页 render_markdown 同一渲染器，
        # 编辑态所见 == 保存后阅读页所见。
        'content_html': render_markdown(md),
        'md_weight': md_weight(md),
        'loss_warning': loss_warning,
        'f_title': pick('title', node.title if node else ''),
        'f_category': pick('category', node.category if node else ''),
        'f_tags': pick('tags', node.tags if node else ''),
        'f_node_type': pick('node_type', (node.node_type if node else 'wiki')),
        'f_parent': pick('parent', str(node.parent_id) if node and node.parent_id else ''),
        'force_checked': submitted is not None,
    }


@csrf_exempt
def api_render_md(request):
    """Markdown → HTML（与阅读页同一渲染器）。

    编辑页「源码 → 所见即所得」切换、粘贴富文本化都走这里，保证编辑态
    与阅读页出自同一条渲染管线，不会出现「编辑里是这样、存完变那样」。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    text = data.get('text') or ''
    return JsonResponse({'ok': True, 'html': render_markdown(text)})


def index(request):
    """树形展示：以 parent 自引用构建知识树（仅展示已采纳的正式知识）。
    若带 ?title= 且命中节点，则直接打开该知识节点（兼容正文 [[双链]] 跳转）。"""
    title_q = (request.GET.get('title') or '').strip()
    if title_q:
        node = KnowledgeNode.objects.filter(title__iexact=title_q).first()
        if node:
            html = render_markdown(node.content_md)
            backlinks = node.edges_in.select_related('source').all()
            outlinks = node.edges_out.select_related('target').all()
            origin = resolve_origin(node.origin_type, node.origin_ref) if node.origin_type else None
            return render(request, 'wiki_node.html', {
                'node': node, 'html': html, 'backlinks': backlinks, 'outlinks': outlinks,
                'origin': origin, 'active_module': 'wiki',
            })
    # 正式知识层只展示已采纳节点；待处理草稿集中在「知识沉淀」页管理
    nodes = list(KnowledgeNode.objects.filter(status='adopted'))
    children_map = {}
    for n in nodes:
        children_map.setdefault(n.parent_id, []).append(n)
    roots = children_map.get(None, [])
    for n in nodes:
        n.children_list = children_map.get(n.pk, [])
    return render(request, 'wiki_index.html', {
        'roots': roots, 'total': len(nodes), 'active_module': 'wiki',
    })


def node_view(request, pk):
    node = get_object_or_404(KnowledgeNode, pk=pk)
    html = render_markdown(node.content_md)
    backlinks = node.edges_in.select_related('source').all()
    outlinks = node.edges_out.select_related('target').all()
    notes = node.notes.all()
    goldens = GoldenSentence.objects.filter(
        Q(node=node) | Q(node__isnull=True, source__icontains=node.title)
    ).distinct()
    origin = resolve_origin(node.origin_type, node.origin_ref) if node.origin_type else None
    return render(request, 'wiki_node.html', {
        'node': node, 'html': html, 'backlinks': backlinks, 'outlinks': outlinks,
        'notes': notes, 'goldens': goldens, 'origin': origin, 'active_module': 'wiki',
    })


def node_edit(request, pk):
    node = get_object_or_404(KnowledgeNode, pk=pk)
    if request.method == 'POST':
        content_md = request.POST.get('content_md', '')
        force = request.POST.get('force_save') == '1'
        dropped, old_w, new_w = content_loss(node.content_md, content_md)
        if dropped and not force:
            # 富文本往返异常（或误删）时拦下：不写库、不落盘，把原样内容回显给用户核对
            pct = int(round((1 - new_w / max(old_w, 1)) * 100))
            log_operation('wiki', 'edit_blocked',
                          detail='%s 正文骤减 %s→%s' % (node.title, old_w, new_w))
            return render(request, 'wiki_node_edit.html', _edit_context(
                node, request, form_md=content_md, submitted=request.POST,
                loss_warning=('本次正文信息量从约 %s 字降到约 %s 字（-约 %d%%），'
                              '已被拦截、未写入。请核对是否误删；确认无误请勾选'
                              '「我已核对内容完整性」后再保存。') % (old_w, new_w, pct),
            ))
        node.title = (request.POST.get('title') or '').strip() or node.title
        node.content_md = content_md
        node.category = (request.POST.get('category') or '').strip()
        node.tags = (request.POST.get('tags') or '').strip()
        node.node_type = request.POST.get('node_type', node.node_type)
        node.parent_id = _safe_parent(request.POST.get('parent'), self_pk=node.pk)
        node.save()
        sync_node_to_file(node)
        auto_link_edges(node)
        log_operation('wiki', 'edit_node', detail=node.title)
        return redirect(f'/wiki/node/{node.pk}/')
    return render(request, 'wiki_node_edit.html', _edit_context(node, request))


def node_create(request):
    if request.method != 'POST':
        return render(request, 'wiki_node_edit.html', _edit_context(None, request))
    base = KnowledgeBase.objects.filter(pk=request.POST.get('base')).first()
    node = KnowledgeNode.objects.create(
        base=base,
        title=(request.POST.get('title') or '').strip() or '未命名',
        content_md=request.POST.get('content_md', ''),
        node_type=request.POST.get('node_type', 'wiki'),
        category=(request.POST.get('category') or '').strip(),
        tags=(request.POST.get('tags') or '').strip(),
        parent_id=_safe_parent(request.POST.get('parent')),
    )
    sync_node_to_file(node)
    auto_link_edges(node)
    log_operation('wiki', 'create_node', detail=node.title)
    return redirect(f'/wiki/node/{node.pk}/')


@csrf_exempt
def api_ask(request):
    """划句问 AI：传入选中文本与问题，返回模型回答（无 Key 时降级提示）。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    text = (data.get('text') or '').strip()
    question = (data.get('question') or '').strip()
    provider = (data.get('provider') or '').strip() or None
    if not text or not question:
        return JsonResponse({'ok': False, 'error': '缺少文本或问题'})

    from core.ai import ask_ai, ai_available
    system = ('你是个人知识库助手，请基于用户提供的资料作答，要求：简洁、准确、用中文，'
              '必要时给出可执行的建议。')
    ans = ask_ai(system, f'资料：\n{text}\n\n问题：{question}', provider_id=provider)
    if not ans:
        ans = ('（当前未配置可用的 AI 服务，无法调用 AI。请在「系统设置 → AI 服务配置』'
               '填写对应服务的 API Key 并启用。）') if not ai_available() else '（调用失败，请检查该服务的 Key / 网络）'
    return JsonResponse({'ok': True, 'answer': ans})


# ---------------------------------------------------------------------------
# 知识沉淀：把知识库 / 办公平台产生的文件交给 AI 提炼，沉淀进 WiKI 层
# ---------------------------------------------------------------------------
def _parse_body(request):
    if request.content_type == 'application/json':
        try:
            return json.loads(request.body)
        except Exception:
            return {}
    return request.POST


def precipitation(request):
    """知识沉淀页：待处理列表 + 发起提炼的来源选择器。"""
    pending = KnowledgeNode.objects.filter(status='pending', ai_generated=True).order_by('-created')
    pending_info = []
    for n in pending:
        pending_info.append({'node': n, 'origin': resolve_origin(n.origin_type, n.origin_ref)})

    # 来源候选（仅列已采纳的正式节点与办公平台产物，避免把草稿再当来源）
    kb_nodes = KnowledgeNode.objects.filter(status='adopted').order_by('-updated')[:80]
    try:
        from office.models import Advise, Report
        advises = Advise.objects.order_by('-created')[:50]
        reports = Report.objects.order_by('-created')[:50]
    except Exception:
        advises = []
        reports = []
    workfiles = WorkFileIndex.objects.order_by('-created')[:50]

    return render(request, 'wiki_precipitation.html', {
        'pending': pending_info,
        'kb_nodes': kb_nodes, 'advises': advises, 'reports': reports, 'workfiles': workfiles,
        'origin_types': ORIGIN_TYPES, 'active_module': 'precipitation',
    })


@csrf_exempt
def api_extract(request):
    """发起提炼：接收 sources=[{type,ref}], 调 AI 抽取核心信息，落地为「待处理」节点。

    约束保证：① 只读原始文档（extract.get_source_content 不写回）；
    ② 产物是全新 KnowledgeNode(status=pending)，不改动任何原始记录；
    ③ 来源以 origin_* 字符串记录（非外键），原始删除不影响本节点。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    data = _parse_body(request)
    raw_sources = data.get('sources') or []
    sources = []
    for s in raw_sources:
        t = (s.get('type') or '').strip()
        r = str((s.get('ref') or '')).strip()
        if t in ORIGIN_TYPES and t != 'multi' and r:
            sources.append((t, r))
    if not sources:
        return JsonResponse({'ok': False, 'error': '未选择有效来源'})
    provider = (data.get('provider') or '').strip() or None
    extracted = extract_knowledge(sources, provider=provider)
    if not extracted:
        return JsonResponse({'ok': False, 'error': '提炼失败：未获得有效内容'})
    node = create_precip_node(sources, extracted, provider=provider)
    if not node:
        return JsonResponse({'ok': False, 'error': '落库失败'})
    return JsonResponse({'ok': True, 'node_id': node.pk, 'title': node.title})


def _is_ajax(request):
    """判断是否为 AJAX / JSON 请求（前端 fetch 已带 X-Requested-With）。"""
    return (request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or request.content_type == 'application/json')


@csrf_exempt
def precipitation_adopt(request, pk):
    """采纳：待处理 -> 已采纳（正式进入知识层，写文件 + 建边）。

    前端走 fetch（X-Requested-With），返回 JSON；若为非 AJAX 的原生表单提交，
    则成功后重定向回沉淀页，避免浏览器停在原始 JSON 文本页（表现为「报错」）。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    node = KnowledgeNode.objects.filter(pk=pk, status='pending').first()
    if not node:
        return JsonResponse({'ok': False, 'error': '未找到待处理草稿（可能已被采纳或删除）'}, status=404)
    try:
        adopt_node(node)
    except Exception as e:
        logger.error('precipitation_adopt failed: %s', e)
        return JsonResponse({'ok': False, 'error': '采纳失败：%s' % e})
    if not _is_ajax(request):
        return redirect('/wiki/precipitation/')
    return JsonResponse({'ok': True})


@csrf_exempt
def precipitation_discard(request, pk):
    """丢弃：删除待处理草稿（及其关系边），不影响任何原始文档。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    node = KnowledgeNode.objects.filter(pk=pk, status='pending').first()
    if not node:
        return JsonResponse({'ok': False, 'error': '未找到待处理草稿（可能已被处理）'}, status=404)
    try:
        discard_node(node)
    except Exception as e:
        logger.error('precipitation_discard failed: %s', e)
        return JsonResponse({'ok': False, 'error': '丢弃失败：%s' % e})
    if not _is_ajax(request):
        return redirect('/wiki/precipitation/')
    return JsonResponse({'ok': True})


@csrf_exempt
def api_grow(request, pk):
    """AI 自生长：对 WiKI 知识节点自动分类 / 补定义 / 补双链。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    node = get_object_or_404(KnowledgeNode, pk=pk)
    provider = (_parse_body(request).get('provider') or '').strip() or None
    data = grow_node(node, provider=provider)
    if not data:
        return JsonResponse({'ok': False, 'error': 'AI 不可用或返回非法结构'})
    changed = []
    if data.get('category'):
        node.category = (data['category'] or '').strip()
        changed.append('分类')
    if data.get('tags'):
        node.tags = (data['tags'] or '').strip()
        changed.append('标签')
    if data.get('definition'):
        if '## 定义' not in (node.content_md or ''):
            node.content_md = (node.content_md or '').rstrip() + '\n\n## 定义\n\n' + data['definition'].strip() + '\n'
            changed.append('定义')
    if data.get('extra_links'):
        from core.services import extract_wikilinks
        existing = extract_wikilinks(node.content_md)
        extra = []
        for lt in data['extra_links']:
            lt = (lt or '').strip()
            if lt and lt not in existing:
                extra.append(lt)
                existing.append(lt)
        if extra:
            node.content_md = (node.content_md or '').rstrip() + '\n\n## 关联知识\n' + ' '.join('[[%s]]' % x for x in extra) + '\n'
            changed.append('双链')
    node.save()
    sync_node_to_file(node)
    auto_link_edges(node)
    log_operation('wiki', 'grow', detail=node.title)
    return JsonResponse({'ok': True, 'changed': changed})
