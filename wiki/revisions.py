import difflib
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from core.models import KnowledgeNode, NodeProposal, DeletedNode
from core.revisions import apply_snapshot


@transaction.atomic
def review(request, pk):
    proposal = get_object_or_404(NodeProposal.objects.select_for_update(), pk=pk)
    node = proposal.node
    if request.method == 'POST':
        if proposal.status != 'pending':
            return JsonResponse({'ok': False, 'error': '候选修订已处理'}, status=409)
        if request.POST.get('action') == 'accept':
            node = KnowledgeNode.objects.select_for_update().get(pk=node.pk)
            if node.version != proposal.base_version:
                return JsonResponse({'ok': False, 'error': '正文已变化，请重新生成候选修订'}, status=409)
            apply_snapshot(node, proposal.proposed)
            proposal.status = 'accepted'
        elif request.POST.get('action') == 'reject':
            proposal.status = 'rejected'
        else:
            return JsonResponse({'ok': False, 'error': '无效操作'}, status=400)
        proposal.save(update_fields=['status'])
        return redirect(f'/wiki/node/{node.pk}/')
    before = node.content_md.splitlines()
    after = proposal.proposed.get('content_md', '').splitlines()
    return render(request, 'revision_review.html', {'node': node, 'proposal': proposal,
        'diff': '\n'.join(difflib.unified_diff(before, after, fromfile='当前正文', tofile='候选正文'))})


@transaction.atomic
def history(request, pk):
    node = get_object_or_404(KnowledgeNode.objects.select_for_update(), pk=pk)
    if request.method == 'POST':
        if request.POST.get('version') != str(node.version):
            return JsonResponse({'ok': False, 'error': '版本已变化，请刷新'}, status=409)
        revision = get_object_or_404(node.revisions, pk=request.POST.get('revision'))
        apply_snapshot(node, revision.snapshot)
        return redirect(f'/wiki/node/{node.pk}/')
    return render(request, 'revision_history.html', {'node': node, 'revisions': node.revisions.order_by('-version')})


def recycle_bin(request):
    if request.method == 'POST':
        from core.services import restore_node
        deleted = get_object_or_404(DeletedNode, pk=request.POST.get('id'), original_id__gt=0)
        node = restore_node(deleted)
        return redirect(f'/wiki/node/{node.pk}/')
    from core.pagination import paginate
    page = paginate(request, DeletedNode.objects.filter(original_id__gt=0).order_by('-created'), 30)
    return render(request, 'recycle_bin.html', {'entries': page, 'page_obj': page})
