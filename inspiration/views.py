"""灵感库：随身小记（类语雀小记）。

任意阅读页划句可经 /inspiration/api/add/ 收入；本页也可直接新建、删除。
"""
import json
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Inspiration, NoteLog
from core.services import log_operation


def _split_title(text):
    """取首行为标题（超 60 字省略），其余行为正文；信息不丢失。"""
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    if not lines:
        return '', ''
    title, body = lines[0], '\n'.join(lines[1:])
    if len(title) > 60:
        if not body:
            body = lines[0]
        title = title[:60] + '…'
    return title, body


def index(request):
    items = Inspiration.objects.all()[:120]
    for it in items:
        it.title, it.body = _split_title(it.text)
    return render(request, 'inspiration.html', {'items': items})


@csrf_exempt
def api_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    text = (data.get('text') or '').strip()
    source = (data.get('source') or '').strip()
    context = (data.get('context') or '').strip()
    if not text:
        return JsonResponse({'ok': False, 'error': '内容为空'})
    Inspiration.objects.create(text=text, source_ref=source, context=context)
    log_operation('inspiration', 'add', detail=source or '划句收入')
    return JsonResponse({'ok': True})


def create(request):
    if request.method == 'POST':
        text = (request.POST.get('text') or '').strip()
        source = (request.POST.get('source') or '').strip()
        context = (request.POST.get('context') or '').strip()
        if text:
            Inspiration.objects.create(text=text, source_ref=source, context=context)
            log_operation('inspiration', 'create', detail=source or '手动')
        return redirect('/inspiration/')
    return redirect('/inspiration/')


def delete_item(request, pk):
    if request.method == 'POST':
        Inspiration.objects.filter(pk=pk).delete()
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False})


def notes_page(request):
    """随笔记：统一录入入口（灵感 / 金句 / 知识库），仅文本输入 + 记入历史。"""
    logs = NoteLog.objects.all()[:80]
    return render(request, 'notes.html', {'logs': logs})


@csrf_exempt
def api_log(request):
    """随笔记记入历史落库：成功写入某库后由前端回调记录。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'})
    target = (data.get('target') or '').strip()
    text = (data.get('text') or '').strip()
    if target not in ('insp', 'gold', 'kb') or not text:
        return JsonResponse({'ok': False, 'error': '参数无效'})
    log = NoteLog.objects.create(target=target, text=text[:2000])
    return JsonResponse({'ok': True, 'id': log.pk,
                         'target_label': log.get_target_display(),
                         'time': log.created.strftime('%m-%d %H:%M')})
