import shutil
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from core.models import Job, KnowledgeNode, CollectionItem
from core.services import sync_node_to_file


def index(request):
    if request.method == 'POST':
        node = get_object_or_404(KnowledgeNode, pk=request.POST.get('node'))
        if sync_node_to_file(node):
            if node.collection_id:
                CollectionItem.objects.filter(pk=node.collection_id).update(status='done', note='文件导出重试完成')
            messages.success(request, '正文文件已重新导出')
        else:
            messages.error(request, '数据库正文仍保留，导出未完成：' + node.export_error)
        return redirect('/settings/diagnostics/')
    return render(request, 'diagnostics.html', {
        'failed_jobs': Job.objects.filter(status='failed').order_by('-created')[:30],
        'active_jobs': Job.objects.filter(status__in=['pending', 'running']).order_by('created')[:30],
        'failed_exports': KnowledgeNode.objects.exclude(export_status='done').order_by('-updated')[:50],
        'free_gb': round(shutil.disk_usage(settings.BASE_DIR).free / 1024**3, 2),
    })
