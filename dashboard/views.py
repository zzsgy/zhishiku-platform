"""总览看板：数据概览 + 趋势图 + 近期动态 + 各模块快捷入口。"""
import json
from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from core.models import KnowledgeBase, KnowledgeNode, CollectionItem, OperationLog, Edge
from inspiration.models import Inspiration
from bookshelf.models import Book, GoldenSentence


def index(request):
    now = timezone.now()
    base_count = KnowledgeBase.objects.count()
    node_count = KnowledgeNode.objects.count()
    collection_count = CollectionItem.objects.count()
    log_count = OperationLog.objects.count()
    insp_count = Inspiration.objects.count()
    book_count = Book.objects.count()
    golden_count = GoldenSentence.objects.count()
    edge_count = Edge.objects.count()

    # 最近 7 天趋势
    days, log_trend, node_trend = [], [], []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date()
        days.append(d.strftime('%m-%d'))
        log_trend.append(OperationLog.objects.filter(created__date=d).count())
        node_trend.append(KnowledgeNode.objects.filter(created__date=d).count())

    recent_logs = OperationLog.objects.all()[:12]
    recent_nodes = KnowledgeNode.objects.all()[:8]

    return render(request, 'dashboard.html', {
        'base_count': base_count, 'node_count': node_count,
        'collection_count': collection_count, 'log_count': log_count,
        'insp_count': insp_count, 'book_count': book_count,
        'golden_count': golden_count, 'edge_count': edge_count,
        'days': json.dumps(days), 'log_trend': json.dumps(log_trend),
        'node_trend': json.dumps(node_trend),
        'recent_logs': recent_logs, 'recent_nodes': recent_nodes,
    })
