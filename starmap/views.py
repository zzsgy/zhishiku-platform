"""知识星图：以 D3 力导向图展示全部知识节点及其关系（Obsidian 式关系网络）。"""
import json
from django.shortcuts import render
from django.db.models import Q

from core.models import KnowledgeNode, Edge


def index(request):
    # 仅展示已采纳的正式知识节点；待处理草稿集中在「知识沉淀」页，不污染关系网络
    query = KnowledgeNode.objects.filter(status='adopted')
    if request.GET.get('base'):
        query = query.filter(base_id=request.GET['base'])
    if request.GET.get('node'):
        center = int(request.GET['node'])
        related = Edge.objects.filter(Q(source_id=center) | Q(target_id=center))
        ids = {center} | set(related.values_list('source_id', flat=True)[:300]) | set(related.values_list('target_id', flat=True)[:300])
        query = query.filter(pk__in=ids)
    total = query.count()
    nodes = list(query.only('id', 'title', 'node_type', 'category').order_by('-updated', '-pk')[:300])
    ids = [node.pk for node in nodes]
    edges = list(Edge.objects.filter(
        source_id__in=ids, target_id__in=ids
    ).order_by('pk')[:1500])
    data = {
        'nodes': [
            {'id': n.pk, 'title': n.title, 'type': n.node_type,
             'category': n.category or ''}
            for n in nodes
        ],
        'links': [
            {'source': e.source_id, 'target': e.target_id, 'label': e.label or ''}
            for e in edges
        ],
    }
    return render(request, 'starmap.html', {
        'graph_data': data,
        'node_count': len(nodes),
        'edge_count': len(edges),
        'total': total, 'limited': total > len(nodes),
        'bases': __import__('core.models', fromlist=['KnowledgeBase']).KnowledgeBase.objects.all(),
    })
