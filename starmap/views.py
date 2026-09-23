"""知识星图：以 D3 力导向图展示全部知识节点及其关系（Obsidian 式关系网络）。"""
import json
from django.shortcuts import render

from core.models import KnowledgeNode, Edge


def index(request):
    # 仅展示已采纳的正式知识节点；待处理草稿集中在「知识沉淀」页，不污染关系网络
    nodes = list(KnowledgeNode.objects.filter(status='adopted'))
    edges = list(Edge.objects.filter(
        source__status='adopted', target__status='adopted'
    ).select_related('source', 'target'))
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
        'graph_json': json.dumps(data, ensure_ascii=False),
        'node_count': len(nodes),
        'edge_count': len(edges),
    })
