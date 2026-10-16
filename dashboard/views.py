"""总览看板：数据概览 + 常用网址导航 + 近期动态 + 各模块快捷入口。"""
import json
from datetime import timedelta
from django.db.models import Count
from django.db.models.functions import TruncDate

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone

from core.models import KnowledgeBase, KnowledgeNode, CollectionItem, OperationLog, Edge, NavCategory, NavSite
from core.security import error
from inspiration.models import Inspiration
from bookshelf.models import Book, GoldenSentence

# 首次进入工作台时预置的常用网址（与参考版式一致，可随时在页面里增删）。
_DEFAULT_NAV = {
    '购物旅游': [
        ('淘宝', 'https://www.taobao.com/'), ('京东商城', 'https://www.jd.com/'),
        ('天猫精选', 'https://www.tmall.com/'), ('苏宁电器', 'https://www.suning.com/'),
        ('聚划算', 'https://juhuasuan.com/'), ('国美', 'https://www.gome.com.cn/'),
        ('亚马逊', 'https://www.amazon.cn/'), ('飞猪旅行', 'https://www.fliggy.com/'),
        ('携程旅行网', 'https://www.ctrip.com/'),
    ],
    '视频音乐': [
        ('斗鱼直播', 'https://www.douyu.com/'), ('哔哩哔哩', 'https://www.bilibili.com/'),
        ('虎牙直播', 'https://www.huya.com/'), ('网易云音乐', 'https://music.163.com/'),
        ('腾讯视频', 'https://v.qq.com/'), ('爱奇艺', 'https://www.iqiyi.com/'),
        ('优酷', 'https://www.youku.com/'),
    ],
    '新闻资讯': [
        ('新浪微博', 'https://weibo.com/'), ('知乎', 'https://www.zhihu.com/'),
        ('百度贴吧', 'https://tieba.baidu.com/'), ('腾讯网', 'https://www.qq.com/'),
        ('凤凰网', 'https://www.ifeng.com/'), ('豆瓣', 'https://www.douban.com/'),
        ('今日头条', 'https://www.toutiao.com/'), ('IT之家', 'https://www.ithome.com/'),
    ],
    '生活必备': [
        ('豆包AI智能助手', 'https://www.doubao.com/'), ('QQ邮箱', 'https://mail.qq.com/'),
        ('163邮箱', 'https://mail.163.com/'), ('谷歌翻译', 'https://translate.google.com/'),
        ('58同城', 'https://www.58.com/'), ('汽车之家', 'https://www.autohome.com.cn/'),
        ('阿里云', 'https://www.aliyun.com/'),
    ],
}


def _ensure_nav_seed():
    """首次访问时预置默认分类与网址；已有数据则不重复导入。"""
    if NavCategory.objects.exists():
        return
    for order, (cat_name, sites) in enumerate(_DEFAULT_NAV.items()):
        cat = NavCategory.objects.create(name=cat_name, order=order)
        NavSite.objects.bulk_create([
            NavSite(category=cat, name=name, url=url, order=i)
            for i, (name, url) in enumerate(sites)
        ])


def _nav_payload():
    categories = [
        {'id': c.pk, 'name': c.name, 'order': c.order,
         'sites': [{'id': s.pk, 'name': s.name, 'url': s.url, 'domain': s.domain} for s in c.sites.all()]}
        for c in NavCategory.objects.all()
    ]
    return categories


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

    _ensure_nav_seed()

    return render(request, 'dashboard.html', {
        'base_count': base_count, 'node_count': node_count,
        'collection_count': collection_count, 'log_count': log_count,
        'insp_count': insp_count, 'book_count': book_count,
        'golden_count': golden_count, 'edge_count': edge_count,
        'days': json.dumps(days), 'log_trend': json.dumps(log_trend),
        'node_trend': json.dumps(node_trend),
        'recent_logs': recent_logs, 'recent_nodes': recent_nodes,
        'nav_categories': _nav_payload(),
    })


# ---------------- 常用网址管理接口 ----------------

def _safe_url(raw):
    """只接受 http/https 链接；前后补协议，防 javascript: 等注入。"""
    url = (raw or '').strip()
    if not url:
        return None
    if not url.lower().startswith(('http://', 'https://')):
        url = 'https://' + url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    return url[:500]


def _json_body_dict(request):
    import json as _json
    try:
        data = _json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def nav_data(request):
    """返回当前分类与网址（页面端重新渲染用）。"""
    return JsonResponse({'ok': True, 'categories': _nav_payload()})


def nav_site_add(request):
    if request.method != 'POST':
        return error('仅支持 POST', 405, 'method_not_allowed')
    data = _json_body_dict(request)
    if data is None:
        return error('请求体必须是 JSON 对象')
    name = (data.get('name') or '').strip()[:64]
    url = _safe_url(data.get('url'))
    if not name:
        return error('请填写网址名称')
    if not url:
        return error('请填写有效的网址（http/https）')
    cat = None
    if data.get('category_id') not in (None, ''):
        cat = NavCategory.objects.filter(pk=data.get('category_id')).first()
    if cat is None:
        cat = NavCategory.objects.order_by('order', 'id').first()
        if cat is None:
            cat = NavCategory.objects.create(name='我的常用', order=0)
    max_order = cat.sites.order_by('-order').values_list('order', flat=True).first() or 0
    site = NavSite.objects.create(category=cat, name=name, url=url, order=max_order + 1)
    return JsonResponse({'ok': True, 'id': site.pk})


def nav_site_edit(request, pk):
    if request.method != 'POST':
        return error('仅支持 POST', 405, 'method_not_allowed')
    data = _json_body_dict(request)
    if data is None:
        return error('请求体必须是 JSON 对象')
    site = get_object_or_404(NavSite, pk=pk)
    name = (data.get('name') or '').strip()[:64]
    url = _safe_url(data.get('url'))
    if name:
        site.name = name
    if url:
        site.url = url
    if data.get('category_id') not in (None, ''):
        cat = NavCategory.objects.filter(pk=data.get('category_id')).first()
        if cat:
            site.category = cat
    site.save()
    return JsonResponse({'ok': True})


def nav_site_delete(request, pk):
    if request.method != 'POST':
        return error('仅支持 POST', 405, 'method_not_allowed')
    site = get_object_or_404(NavSite, pk=pk)
    site.delete()
    return JsonResponse({'ok': True})


def nav_category_add(request):
    if request.method != 'POST':
        return error('仅支持 POST', 405, 'method_not_allowed')
    data = _json_body_dict(request)
    if data is None:
        return error('请求体必须是 JSON 对象')
    name = (data.get('name') or '').strip()[:32]
    if not name:
        return error('请填写分类名')
    if NavCategory.objects.filter(name=name).exists():
        return error('同名分类已存在')
    max_order = NavCategory.objects.order_by('-order').values_list('order', flat=True).first() or 0
    cat = NavCategory.objects.create(name=name, order=max_order + 1)
    return JsonResponse({'ok': True, 'id': cat.pk})


def nav_category_delete(request, pk):
    if request.method != 'POST':
        return error('仅支持 POST', 405, 'method_not_allowed')
    cat = get_object_or_404(NavCategory, pk=pk)
    cat.delete()  # 级联删除分类下的网址
    from django.http import JsonResponse
    return JsonResponse({'ok': True})
