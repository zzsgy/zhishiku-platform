"""自媒体（第 7 项）：选题与文案草稿工作台。

草稿以 KnowledgeNode（category=自媒体）形式进入知识库 09_自媒体，可继续在 WiKI 层编辑。
提供草稿内联编辑 / 删除 / AI 润色（无 Key 时本地降级）。
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.models import KnowledgeBase, KnowledgeNode
from core.services import sync_node_to_file, log_operation, auto_link_edges
from core.ai import ask_ai, ai_available


def index(request):
    nodes = KnowledgeNode.objects.filter(category='自媒体').order_by('-updated')
    return render(request, 'selfmedia.html', {'nodes': nodes})


def create(request):
    if request.method != 'POST':
        return redirect('/selfmedia/')
    platform = (request.POST.get('platform') or '').strip()
    base = KnowledgeBase.objects.filter(kind='knowledge').first()
    node = KnowledgeNode.objects.create(
        base=base,
        title=(request.POST.get('title') or '').strip() or '未命名草稿',
        content_md=request.POST.get('content_md', ''),
        node_type='article',
        category='自媒体',
        tags=(f'平台:{platform}' if platform else ''),
    )
    sync_node_to_file(node)
    auto_link_edges(node)
    log_operation('selfmedia', 'create', detail=node.title)
    return redirect('/selfmedia/')


def edit(request, pk):
    node = get_object_or_404(KnowledgeNode, pk=pk)
    if request.method != 'POST':
        return redirect('/selfmedia/')
    node.title = (request.POST.get('title') or '').strip() or node.title
    node.content_md = request.POST.get('content_md', '')
    node.save()
    sync_node_to_file(node)
    auto_link_edges(node)
    log_operation('selfmedia', 'edit', detail=node.title)
    return redirect('/selfmedia/')


def delete(request, pk):
    node = get_object_or_404(KnowledgeNode, pk=pk)
    if request.method == 'POST':
        log_operation('selfmedia', 'delete', detail=node.title)
        node.delete()
    return redirect('/selfmedia/')


@csrf_exempt
def ai_polish(request, pk):
    """AI 润色草稿：无可用 Key 时返回降级提示。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method'})
    node = get_object_or_404(KnowledgeNode, pk=pk)
    system = ('你是资深新媒体文案顾问，擅长把简短提纲或草稿改写成适合社交平台发布的'
              '吸睛文案：保留原意与要点，语言生动、有画面感，并给出 1-2 句抓眼球的开头。'
              '直接输出润色后的文案正文，不要解释过程。')
    user = (f'选题/标题：{node.title}\n\n原始正文：\n{node.content_md or "（无正文，仅标题）"}\n\n'
            f'请据此润色扩写为一篇可发布的文案。')
    ans = ask_ai(system, user)
    if not ans:
        degraded = True
        ans = ('（当前未配置可用的 AI 服务，无法润色。请在「系统设置 → AI 服务配置」'
               '填写对应服务的 API Key 并启用。）') if not ai_available() else '（AI 调用失败，请检查该服务的 Key / 网络）'
    else:
        degraded = False
    return JsonResponse({'ok': True, 'degraded': degraded, 'polished': ans})
