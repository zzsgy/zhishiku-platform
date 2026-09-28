"""知识库：运行档案库 / 知识库 两库分离的总览与浏览门户。

节点（文章/资料/wiki）的编辑与自生长能力在 WiKI 模块实现。
知识库门户只展示 show_in_kb=True 的库，并挂载金句库、链接库两个外接展示单元。
"""
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q

from core.models import KnowledgeBase, KnowledgeNode, LinkItem, NodeNote
from bookshelf.models import GoldenSentence
from inspiration.models import Inspiration
from core.services import render_markdown, log_operation


def index(request):
    bases = list(KnowledgeBase.objects.filter(show_in_kb=True))
    base_id = request.GET.get('base')
    q = (request.GET.get('q') or '').strip()
    base = KnowledgeBase.objects.filter(pk=base_id, show_in_kb=True).first() if base_id else None

    nodes = KnowledgeNode.objects.filter(base__show_in_kb=True, status='adopted')
    if base:
        nodes = nodes.filter(base=base)
    if q:
        nodes = nodes.filter(Q(title__icontains=q) | Q(content_md__icontains=q))

    stats = {b.pk: KnowledgeNode.objects.filter(base=b, status='adopted').count() for b in bases}
    kind_stats = {}
    for b in bases:
        kind_stats[b.pk] = {
            'wiki': KnowledgeNode.objects.filter(base=b, node_type='wiki', status='adopted').count(),
            'article': KnowledgeNode.objects.filter(base=b, node_type='article', status='adopted').count(),
            'doc': KnowledgeNode.objects.filter(base=b, node_type='doc', status='adopted').count(),
            'note': KnowledgeNode.objects.filter(base=b, node_type='note', status='adopted').count(),
        }
    golden_count = GoldenSentence.objects.count()
    link_pending = LinkItem.objects.filter(status='pending').count()
    link_total = LinkItem.objects.count()
    inspiration_count = Inspiration.objects.count()
    return render(request, 'knowledgebase.html', {
        'bases': bases, 'base': base, 'nodes': nodes[:80],
        'stats': stats, 'kind_stats': kind_stats, 'q': q,
        'golden_count': golden_count,
        'link_pending': link_pending, 'link_total': link_total,
        'inspiration_count': inspiration_count,
    })


def node_view(request, pk):
    node = get_object_or_404(KnowledgeNode, pk=pk)
    html = render_markdown(node.content_md)
    backlinks = node.edges_in.select_related('source').all()
    outlinks = node.edges_out.select_related('target').all()
    notes = node.notes.all()
    # 本文金句：优先显式归属(node FK)，兼容旧数据按 source 标题匹配
    goldens = GoldenSentence.objects.filter(
        Q(node=node) | Q(node__isnull=True, source__icontains=node.title)
    ).distinct()
    from core.extract import resolve_origin
    origin = resolve_origin(node.origin_type, node.origin_ref) if node.origin_type else None
    return render(request, 'wiki_node.html', {
        'node': node, 'html': html, 'backlinks': backlinks, 'outlinks': outlinks,
        'notes': notes, 'goldens': goldens, 'origin': origin, 'active_module': 'knowledgebase',
    })


def add_note(request, pk):
    """知识节点阅读笔记：表单提交入口（与书架读书笔记交互一致）。"""
    if request.method != 'POST':
        return redirect(f'/knowledgebase/node/{pk}/')
    node = get_object_or_404(KnowledgeNode, pk=pk)
    loc = (request.POST.get('location') or '').strip()
    note = (request.POST.get('note') or '').strip()
    if note:
        NodeNote.objects.create(node=node, location=loc, note=note)
        log_operation('knowledgebase', 'node_note', detail=node.title)
    return redirect(f'/knowledgebase/node/{pk}/')


@csrf_exempt
def api_note(request):
    """划词「记笔记」JSON 接口：把选中内容快速写入当前知识节点的阅读笔记。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    node = KnowledgeNode.objects.filter(pk=data.get('node')).first()
    if not node:
        return JsonResponse({'ok': False, 'error': 'node not found'})
    note = (data.get('note') or '').strip()
    if not note:
        return JsonResponse({'ok': False, 'error': '内容为空'})
    obj = NodeNote.objects.create(
        node=node, location=(data.get('location') or '').strip(), note=note)
    log_operation('knowledgebase', 'node_note', detail=node.title)
    return JsonResponse({'ok': True, 'id': obj.pk})


@csrf_exempt
def api_note_delete(request):
    """删除知识节点阅读笔记（阅读面板手动移除）。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    nid = data.get('id')
    if not nid:
        return JsonResponse({'ok': False, 'error': '缺少 id'})
    NodeNote.objects.filter(pk=nid).delete()
    log_operation('knowledgebase', 'node_note_delete', detail='id=%s' % nid)
    return JsonResponse({'ok': True})


def golden(request):
    """金句库：在知识库门户内展示书架汇聚的金句。"""
    items = GoldenSentence.objects.all()[:200]
    return render(request, 'kb_golden.html', {'items': items})


# ---------------------------------------------------------------------------
# 链接库：知识收集中无法解析的链接归档与重试队列
# ---------------------------------------------------------------------------
def _parse_body(request):
    if request.content_type == 'application/json':
        try:
            return json.loads(request.body)
        except Exception:
            return {}
    return request.POST


def link_library(request):
    """链接库：列表 + 多维筛选。

    注：录入功能已整体迁移至「知识收集」模块的链接录入卡片（分类项随迁，
    见 collection_link_action），本页不再提供 POST 新增入口。
    """

    q = (request.GET.get('q') or '').strip()
    src = request.GET.get('source') or ''
    ltype = request.GET.get('link_type') or ''
    status = request.GET.get('status') or ''
    tag = request.GET.get('tag') or ''
    domain = request.GET.get('domain') or ''

    qs = LinkItem.objects.all()
    if q:
        qs = qs.filter(Q(url__icontains=q) | Q(title__icontains=q))
    if src:
        qs = qs.filter(source=src)
    if ltype:
        qs = qs.filter(link_type=ltype)
    if status:
        qs = qs.filter(status=status)
    if tag:
        qs = qs.filter(tags__icontains=tag)
    if domain:
        qs = qs.filter(domain=domain)

    pending = LinkItem.objects.filter(status='pending').count()
    archived = LinkItem.objects.filter(status='archived').count()
    invalid = LinkItem.objects.filter(status='invalid').count()
    total = LinkItem.objects.count()
    domains = list(LinkItem.objects.values_list('domain', flat=True).distinct()[:30])

    return render(request, 'kb_links.html', {
        'items': qs[:200],
        'pending': pending, 'archived': archived, 'invalid': invalid, 'total': total,
        'domains': domains,
        'source_choices': LinkItem.SOURCE_CHOICES,
        'type_choices': LinkItem.TYPE_CHOICES,
        'reason_choices': LinkItem.REASON_CHOICES,
        'status_choices': LinkItem.STATUS_CHOICES,
        'filters': {'q': q, 'source': src, 'link_type': ltype, 'status': status, 'tag': tag, 'domain': domain},
    })


@csrf_exempt
def link_status(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    item = get_object_or_404(LinkItem, pk=pk)
    data = _parse_body(request)
    status = (data.get('status') or '').strip()
    if status not in dict(LinkItem.STATUS_CHOICES):
        return JsonResponse({'ok': False, 'error': '无效状态'})
    item.status = status
    item.last_checked = timezone.now()
    item.save()
    log_operation('link_library', 'status', detail='%s -> %s' % (item.url, status))
    return JsonResponse({'ok': True, 'status': item.status})


@csrf_exempt
def link_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    item = get_object_or_404(LinkItem, pk=pk)
    item.delete()
    log_operation('link_library', 'delete', detail=item.url)
    return JsonResponse({'ok': True})


@csrf_exempt
def link_retry(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    item = get_object_or_404(LinkItem, pk=pk)
    item.retry_count += 1
    item.last_checked = timezone.now()
    reason = ''
    try:
        from core.parsers import parse_web_page
        from core.models import CollectionItem
        from core.services import default_base, link_collection_to_node
        title, md = parse_web_page(item.url, 'auto')
        base = item.related_base or default_base('knowledge')
        ci = CollectionItem.objects.create(
            kind='web', title=item.title or title, parsed_md=md,
            status='done', base=base, source_url=item.url)
        link_collection_to_node(ci, base=base, title=item.title or title, category='网页解析')
        item.status = 'archived'
        reason = '解析成功并接入知识库'
    except Exception as e:
        item.unparse_reason = 'unknown'
        reason = '仍失败：%s' % str(e)[:120]
    item.save()
    log_operation('link_library', 'retry', detail='%s -> %s' % (item.url, reason))
    return JsonResponse({'ok': True, 'status': item.status, 'reason': reason})


def node_delete(request, pk):
    """删除知识节点：级联删除其阅读笔记(NodeNote)与关系边(Edge)。"""
    if request.method != 'POST':
        return redirect('/knowledgebase/')
    node = get_object_or_404(KnowledgeNode, pk=pk)
    base_pk = node.base.pk if node.base else None
    title = node.title
    node.delete()  # 级联删除 notes 与 edges
    log_operation('knowledgebase', 'node_delete', detail=title)
    if base_pk:
        return redirect(f'/knowledgebase/?base={base_pk}')
    return redirect('/knowledgebase/')
