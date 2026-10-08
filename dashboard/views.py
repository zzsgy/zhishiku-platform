"""总览看板：数据概览 + 趋势图 + 近期动态 + 各模块快捷入口。"""
import json
from datetime import timedelta
from django.db.models import Count
from django.db.models.functions import TruncDate

from django.shortcuts import render
from django.utils import timezone

from core.models import KnowledgeBase, KnowledgeNode, CollectionItem, OperationLog, Edge
from inspiration.models import Inspiration
from bookshelf.models import Book, GoldenSentence


def index(request):
    now = timezone.now()
    base_count = KnowledgeBase.objects.count()
    node_count = KnowledgeNode.objects.filter(status='adopted').count()
    collection_count = CollectionItem.objects.count()
    log_count = OperationLog.objects.count()
    insp_count = Inspiration.objects.count()
    book_count = Book.objects.count()
    golden_count = GoldenSentence.objects.count()
    edge_count = Edge.objects.count()

    first_day = timezone.localdate(now) - timedelta(days=6)
    log_counts = {row['day']: row['n'] for row in OperationLog.objects.filter(created__date__gte=first_day).order_by().annotate(day=TruncDate('created')).values('day').annotate(n=Count('id'))}
    node_counts = {row['day']: row['n'] for row in KnowledgeNode.objects.filter(status='adopted', created__date__gte=first_day).order_by().annotate(day=TruncDate('created')).values('day').annotate(n=Count('id'))}
    dates = [first_day + timedelta(days=i) for i in range(7)]
    days = [day.strftime('%m-%d') for day in dates]
    log_trend = [log_counts.get(day, 0) for day in dates]
    node_trend = [node_counts.get(day, 0) for day in dates]
    recent_logs = OperationLog.objects.all()[:12]
    recent_nodes = KnowledgeNode.objects.filter(status='adopted')[:8]

    return render(request, 'dashboard.html', {
        'base_count': base_count, 'node_count': node_count,
        'collection_count': collection_count, 'log_count': log_count,
        'insp_count': insp_count, 'book_count': book_count,
        'golden_count': golden_count, 'edge_count': edge_count,
        'days': json.dumps(days), 'log_trend': json.dumps(log_trend),
        'node_trend': json.dumps(node_trend),
        'recent_logs': recent_logs, 'recent_nodes': recent_nodes,
    })
