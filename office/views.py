"""办公平台：行政管理 / 基层党建 / 网络运维 三类工作台。

能力：
- 工作记录（状态、事宜；归档视图 rec 只作用于本区）
- 工作文件目录（分类卡片 cat + 关键词 wfq 只作用于本区，与工作记录互不影响）
- 统一搜索（osq + oscope：可分别搜「文件 / 献策 / 报告」，也可合并为「全部」）
- 全网检索（search_web；入口固定在全局顶栏，见 templates/base.html）
- 按知识库 + 个人风格献策（通义千问；无 Key 降级为本地汇总）——产出落库 + 落盘，可再次找回
- 日报 / 周报 / 月报 / 年报生成——同样落库 + 落盘
"""
import json
import os
import mimetypes
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urlparse

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, FileResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Count, Q
from django.utils.html import escape

from core.models import KnowledgeNode, SystemConfig, WorkFileIndex
from .models import Advise, Report, WorkRecord
from .storage import (advise_dir, display_path, file_exists, office_output_root,
                      report_dir, save_advise_file, save_report_file)
from core.ai import ask_qwen
from core.parsers import search_web
from core.services import log_operation, render_markdown
from core.workfolder import workfile_root


CATEGORY_LABELS = dict(WorkRecord.CATEGORY)      # 含归档分类：记录类型展示 + 新建记录下拉
KIND_LABELS = dict(Report.KIND)
STATUS_OPTIONS = ['进行中', '待定', '已完成']
# 报告类型按钮的图标与口径提示（仅影响「生成报告」模块的按钮呈现）
KIND_ICONS = {'daily': 'calendar-day', 'weekly': 'calendar-week',
              'monthly': 'calendar-month', 'yearly': 'calendar-range'}
KIND_HINTS = {'daily': '单日 · 取结束日期',
              'weekly': '按所选区间汇总',
              'monthly': '按所选区间汇总',
              'yearly': '按所选区间汇总'}
# 归档分类（WorkRecord.CATEGORY 的第 4 项）：日期卡片上的「归档」按钮把该日记录移入此类。
# 它同时是「工作记录」区的视图标识（URL 参数 rec，见 index）。
ARCHIVE_CAT = WorkRecord.ARCHIVE_KEY
ARCHIVE_LABEL = CATEGORY_LABELS[ARCHIVE_CAT]
# 三个工作分类 = 「工作文件目录」的分类筛选入口（归档分类不参与文件筛选）
WORK_CATS = {k: v for k, v in CATEGORY_LABELS.items() if k != ARCHIVE_CAT}


WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

# ---------------------------------------------------------------------------
# 统一搜索（办公平台页面最前部的检索面板）
#   osq    = 关键词
#   oscope = 作用范围：all（三类合并）/ file（工作文件）/ advise（AI 献策）/ report（报告）
# 与「工作文件目录」内的 wfq 是两件事：wfq 只过滤文件目录模块，oscope=file 只影响搜索结果。
# ---------------------------------------------------------------------------
SEARCH_SCOPE_ORDER = ['all', 'file', 'advise', 'report']
SEARCH_SCOPES = {
    'all': {'label': '全部', 'icon': 'search'},
    'file': {'label': '搜文件', 'icon': 'file-earmark-text'},
    'advise': {'label': '搜献策', 'icon': 'stars'},
    'report': {'label': '搜报告', 'icon': 'file-earmark-bar-graph'},
}
SEARCH_LIMIT = 20          # 每个范围最多展示多少条
SNIPPET_WIDTH = 110        # 命中片段截取宽度


def _snippet(text, q, width=SNIPPET_WIDTH):
    """截取关键词附近的片段并高亮命中词（先转义再插 <mark>，保证不引入 XSS）。"""
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if not text:
        return ''
    idx = text.lower().find(q.lower()) if q else -1
    start = max(0, idx - width // 3) if idx >= 0 else 0
    frag = text[start:start + width]
    frag = escape(frag)
    if q:
        frag = re.sub('(' + re.escape(escape(q)) + ')', r'<mark>\1</mark>', frag, flags=re.I)
    return ('…' if start > 0 else '') + frag + ('…' if start + width < len(text) else '')


def _search_files(q):
    """工作文件：标题或提取内容命中。"""
    qs = WorkFileIndex.objects.filter(
        Q(title__icontains=q) | Q(extracted__icontains=q)).order_by('-updated')
    out = []
    for wf in qs[:SEARCH_LIMIT]:
        out.append({
            'title': wf.title,
            'snippet': _snippet(wf.extracted, q),
            'meta': ' · '.join(x for x in [
                wf.category,
                ('.%s' % wf.ext) if wf.ext else '',
                wf.updated.strftime('%Y-%m-%d') if wf.updated else '',
            ] if x),
            'url': '/office/workfile/%d/' % wf.pk,
        })
    return out, qs.count()


def _search_advises(q):
    """AI 献策：主题或正文命中。"""
    qs = Advise.objects.filter(
        Q(topic__icontains=q) | Q(content_md__icontains=q)).order_by('-created')
    out = []
    for adv in qs[:SEARCH_LIMIT]:
        out.append({
            'title': adv.topic,
            'snippet': _snippet(adv.content_md, q),
            'meta': ' · '.join(x for x in [
                adv.get_category_display(),
                adv.created.strftime('%Y.%m.%d %H:%M') if adv.created else '',
            ] if x),
            'url': '/office/advise/%d/' % adv.pk,
        })
    return out, qs.count()


def _search_reports(q):
    """报告：正文命中；若关键词本身就是报告类型名（日报/周报/月报/年报）也一并命中。"""
    cond = Q(content_md__icontains=q)
    kind_hits = [k for k, label in KIND_LABELS.items() if q in label or label in q]
    if kind_hits:
        cond |= Q(kind__in=kind_hits)
    qs = Report.objects.filter(cond).order_by('-created')
    out = []
    for rep in qs[:SEARCH_LIMIT]:
        out.append({
            'title': rep.title,
            'snippet': _snippet(rep.content_md, q),
            'meta': ' · '.join(x for x in [
                rep.get_kind_display(),
                rep.created.strftime('%Y.%m.%d %H:%M') if rep.created else '',
            ] if x),
            'url': '/office/report/%d/' % rep.pk,
        })
    return out, qs.count()


def _office_search(q, scope):
    """按范围检索三类产出物，返回模板直接可用的结构。

    无论当前选了哪个范围，**三类命中数都会真实统计**（三个 count + 受限取数，开销很小），
    这样范围按钮上的角标始终能反映「换到那一类会有多少条」，用户不必逐个点过去试。
    但结果明细只渲染当前范围（scope=all 时三类合并、分组展示）。
    """
    hits = {'file': [], 'advise': [], 'report': []}
    counts = {'file': 0, 'advise': 0, 'report': 0}
    if q:
        hits['file'], counts['file'] = _search_files(q)
        hits['advise'], counts['advise'] = _search_advises(q)
        hits['report'], counts['report'] = _search_reports(q)

    wanted = SEARCH_SCOPE_ORDER[1:] if scope == 'all' else [scope]
    groups = [{
        'key': key,
        'label': SEARCH_SCOPES[key]['label'],
        'icon': SEARCH_SCOPES[key]['icon'],
        'items': hits[key],
        'count': counts[key],
        'more': counts[key] > len(hits[key]),
        'limit': SEARCH_LIMIT,
    } for key in wanted]

    scopes = [{
        'key': key,
        'label': SEARCH_SCOPES[key]['label'],
        'icon': SEARCH_SCOPES[key]['icon'],
        'active': key == scope,
        'count': sum(counts.values()) if key == 'all' else counts[key],
    } for key in SEARCH_SCOPE_ORDER]
    return {
        'q': q,
        'scope': scope,
        'scope_label': SEARCH_SCOPES[scope]['label'],
        'scopes': scopes,
        'groups': groups,
        'counts': counts,
        'total': sum(counts.values()),                              # 三类合计（范围按钮角标用）
        'scope_total': sum(counts[k] for k in wanted),               # 当前范围命中总数
        'shown': sum(len(hits[k]) for k in wanted),                  # 当前范围实际渲染的条数
        'active': bool(q),
    }


def _group_records(records):
    """按天分组工作记录，供模板按日容器展示。

    can_archive：本日是否还有未归档记录——决定该日卡片是否显示「归档」按钮。
    """
    today = date.today()
    groups = []
    for r in records:
        if not groups or groups[-1]['date'] != r.date:
            groups.append({
                'date': r.date,
                'label': r.date.strftime('%m-%d'),
                'full': r.date.strftime('%Y-%m-%d'),
                'weekday': WEEKDAY_CN[r.date.weekday()],
                'is_today': r.date == today,
                'can_archive': False,
                'items': [],
            })
        groups[-1]['items'].append(r)
        if r.category != ARCHIVE_CAT:
            groups[-1]['can_archive'] = True
    return groups


def _office_url(anchor, cat='', rec='', wfq='', osq='', oscope=''):
    """拼一个 /office/ 链接。各筛选参数彼此独立，空值自动省略：

    cat    = 工作分类（只作用于「工作文件目录」）
    rec    = 记录视图（只作用于「工作记录」区）
    wfq    = 文件关键词（只作用于「工作文件目录」）
    osq    = 统一搜索关键词（只作用于页面最前部的搜索面板）
    oscope = 统一搜索范围 all / file / advise / report
    """
    parts = []
    if cat:
        parts.append('cat=%s' % cat)
    if rec:
        parts.append('rec=%s' % rec)
    if wfq:
        parts.append(urlencode({'wfq': wfq}))
    if osq:
        parts.append(urlencode({'osq': osq}))
        if oscope and oscope != 'all':
            parts.append('oscope=%s' % oscope)
    qs = '&'.join(parts)
    return '/office/%s#%s' % (('?' + qs) if qs else '', anchor)


def index(request):
    """办公平台首页：文件分类筛选 + 文件关键词检索 + 记录归档视图。

    三个筛选维度各自独立、互不覆盖，**作用范围严格区分**：

    - cat = 工作分类（行政管理 / 基层党建 / 网络运维），**只作用于「工作文件目录」**，
      与「工作记录」区完全无关（记录区既不过滤、也不显示分类态）。互斥单选（切换即替换），
      再次点击已选中的卡片 = 取消该分类。入口是下方的三张分类卡片。
    - wfq = 文件关键词，由工作文件目录内的检索框驱动，同样**只作用于「工作文件目录」**；
      与 cat 同时存在时**叠加生效（AND）**：先按分类缩小到该类文件，再在该类内匹配关键词。
    - rec = 记录视图，取值 archive 时「工作记录」区只显示已归档记录，**只作用于「工作记录」区**，
      与 cat / wfq 互不影响。归档只是 WorkRecord.category 的改写，既不产生 WorkFileIndex
      也不落磁盘文件，所以文件目录里本就没有记录可筛。入口是「工作记录」标题旁的归档按钮。
    - osq + oscope = 统一搜索，**只作用于页面最前部的搜索面板**（作用于搜索结果本身，
      不改动下方任何一个模块的数据）：oscope=file 搜工作文件、advise 搜 AI 献策、report 搜报告、
      all 三类合并。它和「工作文件目录」内的 wfq 是两件事——wfq 是模块级过滤，osq 是跨类检索。

    默认（非归档视图）下，工作记录列表排除归档分类。
    工作记录一律只在「工作记录」区呈现；「工作文件目录」只列工作文件，不列任何记录。
    """
    raw_cat = (request.GET.get('cat') or '').strip()
    rec = (request.GET.get('rec') or '').strip()
    if raw_cat == ARCHIVE_CAT:
        # 兼容历史书签「?cat=archive」：归档已改为独立的记录视图，等价于「?rec=archive」
        rec = ARCHIVE_CAT
    cat = raw_cat if raw_cat in WORK_CATS else ''
    if rec != ARCHIVE_CAT:
        rec = ''
    archive_view = bool(rec)
    cat_label = WORK_CATS.get(cat, '')
    wfq = (request.GET.get('wfq') or '').strip()
    # 统一搜索（页面最前部的检索面板）：只作用于搜索结果本身，不参与 cat / wfq / rec
    osq = (request.GET.get('osq') or '').strip()
    oscope = (request.GET.get('oscope') or 'all').strip()
    if oscope not in SEARCH_SCOPES:
        oscope = 'all'
    search = _office_search(osq, oscope)

    # 维度一：记录视图 → 工作记录（分类筛选不参与，记录区与分类卡片彻底解耦）
    records = WorkRecord.objects.all()
    if archive_view:
        records = records.filter(category=ARCHIVE_CAT)
    else:
        records = records.exclude(category=ARCHIVE_CAT)
    records = list(records[:60])
    day_groups = _group_records(records)
    record_shown = len(records)
    # 报告列表：新生成的排在前面（Report.Meta 不设 ordering，避免迁移状态漂移）
    reports = list(Report.objects.order_by('-created')[:20])
    # 献策列表：同样新生成的排在前面，作为「返回后再次找到输出文件」的主入口
    advises = list(Advise.objects.order_by('-created')[:20])
    # 「生成报告」模块的默认区间 = 本月 1 日 ~ 今天（用户可在表单里改成任意区间）
    report_default_start = date.today().replace(day=1).strftime('%Y-%m-%d')
    report_default_end = date.today().strftime('%Y-%m-%d')

    # 「工作记录」标题旁归档入口的条数
    archive_count = WorkRecord.objects.filter(category=ARCHIVE_CAT).count()

    # 维度二：工作分类 & 关键词 → 工作文件目录（叠加 AND）。cat 全流程只在此处被消费。
    wf_all = WorkFileIndex.objects.all()
    wf_qs = wf_all
    if cat_label:
        wf_qs = wf_qs.filter(category=cat_label)
    if wfq:
        wf_qs = wf_qs.filter(Q(title__icontains=wfq) | Q(extracted__icontains=wfq))
    workfiles_grouped = _group_workfiles(wf_qs)
    wf_filtered = bool(cat_label or wfq)   # 文件目录是否处于筛选态

    # 分类卡片：只统计工作文件数（不再出现记录数，避免筛选入口与记录区耦合）
    file_counts = {row['category']: row['n'] for row in
                   WorkFileIndex.objects.values('category').annotate(n=Count('id'))}
    cat_cards = [{'key': k, 'label': lbl,
                  'desc': '记录与跟进本类工作事宜，沉淀为可检索的知识。',
                  'files': file_counts.get(lbl, 0),
                  'enter_url': _office_url('workfiles', cat=k, wfq=wfq, rec=rec,
                                           osq=osq, oscope=oscope),
                  'exit_url': _office_url('workfiles', wfq=wfq, rec=rec,
                                          osq=osq, oscope=oscope)}
                 for k, lbl in WORK_CATS.items()]

    # 文件目录的筛选提示（分类 + 关键词；不含记录视图，作用范围一目了然）
    wf_filters = []
    if cat_label:
        wf_filters.append('分类：%s' % cat_label)
    if wfq:
        wf_filters.append('关键词：%s' % wfq)

    return render(request, 'office.html', {
        'day_groups': day_groups,
        'record_shown': record_shown,
        'reports': reports,
        'report_count': len(reports),
        'advises': advises,
        'advise_count': len(advises),
        'report_default_start': report_default_start,
        'report_default_end': report_default_end,
        'kind_cards': [{'key': k, 'label': lbl,
                        'icon': KIND_ICONS.get(k, 'file-earmark-text'),
                        'hint': KIND_HINTS.get(k, '')}
                       for k, lbl in KIND_LABELS.items()],
        'cats': CATEGORY_LABELS, 'kinds': KIND_LABELS,
        'status_opts': STATUS_OPTIONS,
        'cat_cards': cat_cards,
        'active_cat': cat,
        'active_cat_label': cat_label,
        'active_rec': rec,
        'archive_view': archive_view,
        'wf_filters': wf_filters,
        'archive_label': ARCHIVE_LABEL,
        'archive_count': archive_count,
        # 归档视图入口：进入 / 退出都保留文件目录的分类与关键词——归档只切换记录区，
        # 不应顺带重置下方工作文件目录的筛选。
        'archive_enter_url': _office_url('work-records', cat=cat, wfq=wfq, rec=ARCHIVE_CAT,
                                         osq=osq, oscope=oscope),
        'archive_exit_url': _office_url('work-records', cat=cat, wfq=wfq,
                                        osq=osq, oscope=oscope),
        'workfiles_grouped': workfiles_grouped,
        'workfile_total': wf_qs.count(),
        'workfile_all_total': wf_all.count(),
        'wf_filtered': wf_filtered,
        # 文件检索框旁「清除关键词」与筛选条「清除全部筛选」：只清文件筛选，保留记录视图
        'clear_wfq_url': _office_url('workfiles', cat=cat, rec=rec, osq=osq, oscope=oscope),
        'clear_filters_url': _office_url('workfiles', rec=rec, osq=osq, oscope=oscope),
        'wfq': wfq,
        # 统一搜索结果面板
        'search': search,
        'clear_search_url': _office_url('office-search', cat=cat, rec=rec, wfq=wfq),
        # 产出物的存放路径提示（与 office/storage.py 的规则同源，避免两处各写一份）
        'advise_output_root': display_path(advise_dir()),
        'report_output_root': display_path(report_dir()),
    })


def _group_workfiles(qs):
    """把工作文件索引按分类分组，并返回每组的计数与条目（按更新时间倒序）。"""
    groups = {}
    for wf in qs.order_by('-updated'):
        groups.setdefault(wf.category, []).append(wf)
    # 分类按名称排序，组内按更新时间倒序
    return [{'category': c, 'items': items}
            for c, items in sorted(groups.items(), key=lambda kv: kv[0])]


def work_add(request):
    if request.method != 'POST':
        return redirect('/office/')
    new_cat = (request.POST.get('category') or '').strip()
    if new_cat not in CATEGORY_LABELS:      # 白名单：避免伪造/失效分类写入
        new_cat = 'admin'
    WorkRecord.objects.create(
        category=new_cat,
        content=(request.POST.get('content') or '').strip(),
        status=(request.POST.get('status') or '进行中').strip(),
    )
    log_operation('office', 'work_add')
    # 保留记录视图与文件关键词，新增记录不重置页面状态。
    # 分类筛选不再随表单传递——它只作用于工作文件目录，与记录区无关。
    rec = (request.POST.get('rec') or '').strip()
    wfq = (request.POST.get('wfq') or '').strip()
    params = []
    if rec == ARCHIVE_CAT:
        params.append('rec=%s' % rec)
    if wfq:
        params.append(urlencode({'wfq': wfq}))
    suffix = ('?' + '&'.join(params)) if params else ''
    return redirect('/office/%s#work-records' % suffix)


# ---------------------------------------------------------------------------
# 全局聚合检索（顶栏入口，/office/search/）
#   scopes = 逗号分隔的组合：kb（知识库）/ office（办公平台）/ web（全网），
#            缺省或 all = 三者全选。分组顺序固定：本地快源在前、联网慢源在后。
# ---------------------------------------------------------------------------
GLOBAL_SCOPE_ORDER = ['kb', 'office', 'web']
GLOBAL_SCOPES = {
    'kb': {'label': '知识库', 'icon': 'bi-archive'},
    'office': {'label': '办公平台', 'icon': 'bi-briefcase'},
    'web': {'label': '全网', 'icon': 'bi-globe2'},
}
GLOBAL_LIMIT = 8      # 知识库 / 全网每组最多展示条数（办公平台沿用统一搜索的 SEARCH_LIMIT）


def _search_kb(q):
    """知识库节点：标题或正文命中（只搜「展示中」的知识库）。"""
    qs = KnowledgeNode.objects.filter(base__show_in_kb=True).filter(
        Q(title__icontains=q) | Q(content_md__icontains=q))
    out = []
    for n in qs[:GLOBAL_LIMIT]:
        meta = ' · '.join(x for x in [
            n.base.name if n.base else '',
            n.get_node_type_display(),
            n.updated.strftime('%Y.%m.%d') if n.updated else '',
        ] if x)
        out.append({
            'title': n.title,
            'snippet': _snippet(n.content_md, q),
            'meta': meta,
            'url': '/knowledgebase/node/%d/' % n.pk,
            'src': GLOBAL_SCOPES['kb']['label'],
            'safe': True,          # _snippet 已转义并插 <mark>
        })
    return out, qs.count()


def _search_web_items(q):
    """全网检索：Bing 结果解析，出处注明域名。外部内容不做 |safe（防注入）。"""
    raw = search_web(q, limit=GLOBAL_LIMIT)
    out = []
    for r in raw:
        host = urlparse(r.get('url') or '').netloc or '互联网'
        out.append({
            'title': r.get('title', ''),
            'snippet': r.get('snippet', ''),
            'meta': host,
            'url': r.get('url', ''),
            'src': GLOBAL_SCOPES['web']['label'],
            'safe': False,
        })
    return out, len(raw)


def _global_search(q, scopes):
    """按所选范围聚合检索。

    每个选中的范围都**真实执行**；0 命中也保留分组并明示（聚合透明，
    用户能区分「没搜」和「搜了没有」）。返回 (分组列表, 总命中数)。
    """
    groups, total = [], 0
    for key in scopes:
        conf = GLOBAL_SCOPES[key]
        if key == 'kb':
            items, n = _search_kb(q)
            groups.append({**conf, 'key': key, 'items': items, 'subs': [],
                           'count': n, 'more': n > len(items), 'limit': GLOBAL_LIMIT})
        elif key == 'office':
            data = _office_search(q, 'all')      # 复用统一搜索：文件/献策/报告三小节
            groups.append({**conf, 'key': key, 'items': [], 'subs': data['groups'],
                           'count': data['total'], 'more': False, 'limit': 0})
        else:
            items, n = _search_web_items(q)
            groups.append({**conf, 'key': key, 'items': items, 'subs': [],
                           'count': n, 'more': n > len(items), 'limit': GLOBAL_LIMIT})
        total += groups[-1]['count']
    return groups, total


def search(request):
    """聚合检索结果页（入口固定在全局顶栏，任意页面均可发起）。

    scopes 决定搜哪些范围：kb / office / web 的任意组合（缺省 = 全选）。
    各范围结果分组聚合展示，组内顺序固定：知识库 → 办公平台 → 全网，
    每条结果都注明出处（范围徽标 + 来源 meta）。关键词回填顶栏输入框
    （global_search_q），保证在结果页上仍可直接改词再次检索。
    """
    q = (request.GET.get('q') or '').strip()
    raw = (request.GET.get('scopes') or '').strip()
    if not raw or raw == 'all':
        scopes = list(GLOBAL_SCOPE_ORDER)
    else:
        wanted = {s for s in raw.split(',') if s in GLOBAL_SCOPES}   # 白名单过滤
        scopes = [s for s in GLOBAL_SCOPE_ORDER if s in wanted] or list(GLOBAL_SCOPE_ORDER)
    groups, total = [], 0
    if q:
        groups, total = _global_search(q, scopes)
        log_operation('office', 'search', detail='%s [%s]' % (q, '+'.join(scopes)))
    return render(request, 'office_search.html', {
        'q': q, 'scopes': scopes, 'scope_conf': GLOBAL_SCOPES,
        'groups': groups, 'total': total, 'global_search_q': q,
    })


def _persona_block():
    """组装「个人设定 / 写作风格 / 输出禁忌」提示词片段。

    唯一出口：献策与报告共用，避免两处口径漂移。
    三段内容在「系统设置 → 个人设定与写作风格」维护。
    """
    segs = []
    profile = SystemConfig.get_value('user_profile', '').strip()
    style = SystemConfig.get_value('user_style', '').strip()
    taboo = SystemConfig.get_value('user_taboo', '').strip()
    if profile:
        segs.append('【我的个人设定】\n' + profile)
    if style:
        segs.append('【我的写作风格】\n' + style)
    if taboo:
        segs.append('【输出禁忌】\n' + taboo)
    return '\n\n'.join(segs)


def advise(request):
    """生成献策：走 AI（无 Key 时本地汇总降级），随后**落库 + 落盘**再跳转详情页。

    之所以要落库落盘：以前是「生成即渲染一个临时页面」，用户返回 / 关闭后就再也找不回来。
    现在每一次生成都会：
      1) 落一条 Advise 记录（前端列表与统一搜索的权威索引）；
      2) 写一份 Markdown 到 03_知识库/10_办公平台/献策输出/<工作分类>/<日期>_<主题>.md；
      3) 302 跳到 /office/advise/<pk>/ 详情页（仍保持「输出后自动打开」的体验，
         但这次的打开对象是**可再次访问的固定地址**，不是一次性渲染结果）。
    """
    if request.method != 'POST':
        return redirect('/office/')
    topic = (request.POST.get('topic') or '').strip()
    cat = (request.POST.get('category') or 'admin').strip()
    if cat not in CATEGORY_LABELS:          # 白名单：避免伪造分类写库 / 落盘越界
        cat = 'admin'

    related = KnowledgeNode.objects.filter(title__icontains=topic)[:6]
    kb_ctx = '\n'.join(f'- 《{n.title}》：{(n.content_md or "")[:300]}' for n in related)
    style = SystemConfig.get_value('user_style', '')
    persona = _persona_block()
    # persona 已内含【我的写作风格】，此处仅在其为空时回退，避免同一份风格重复注入
    style_line = f'我的个人表达风格：{style}\n' if (style and not persona) else ''

    prompt = (
        f'主题：{topic}\n工作类别：{CATEGORY_LABELS.get(cat)}\n'
        f'{style_line}'
        f'{persona + chr(10) + chr(10) if persona else ""}'
        f'我的知识库相关资料：\n{kb_ctx or "（无直接相关资料）"}\n\n'
        f'请严格遵循上述个人设定、写作风格与输出禁忌，结合资料给出务实、可落地的建议与行动方案（分点、有优先级）。'
    )
    ans = ask_qwen(
        '你是我的专属工作参谋，熟悉我的知识库与个人表达风格，善于把信息转化为可执行建议。',
        prompt,
    )
    used_ai = bool(ans)
    if not used_ai:
        ans = (
            f'# 关于「{topic}」的本地汇总（未配置通义千问 API Key）\n\n'
            f'> 配置 Key 后将基于知识库与你的风格生成深度献策。\n\n'
            f'## 知识库相关资料\n{kb_ctx or "（未检索到相关知识库内容）"}'
        )

    cat_label = CATEGORY_LABELS.get(cat, '')
    source_label = 'AI 生成' if used_ai else '本地汇总'
    adv = Advise.objects.create(
        category=cat, topic=topic or '未命名主题', content_md=ans,
        source='ai' if used_ai else 'local',
    )
    # 落盘失败不影响流程：数据库记录已足够支撑列表 / 搜索 / 再次打开
    path = save_advise_file(adv.topic, cat_label, ans, day=adv.created.date(),
                            source_label=source_label)
    if path:
        adv.file_path = path
        adv.save(update_fields=['file_path'])
    log_operation('office', 'advise', detail=adv.topic)
    return redirect(f'/office/advise/{adv.pk}/')


def advise_view(request, pk):
    """献策详情：正文 + 输出文件位置 + 返回入口，供「返回 / 关闭后再次找到」。"""
    adv = get_object_or_404(Advise, pk=pk)
    cat_label = CATEGORY_LABELS.get(adv.category, '')
    # 知识库参考：按主题即时回查，保持与生成时的取数口径一致（不额外建快照表）
    related = KnowledgeNode.objects.filter(title__icontains=adv.topic)[:6]
    return render(request, 'office_advise.html', {
        'adv': adv,
        'html': render_markdown(adv.content_md),
        'cat_label': cat_label,
        'source_label': adv.get_source_display(),
        'related': related,
        'out_dir': display_path(advise_dir(cat_label)),
        'out_path': display_path(adv.file_path),
        'file_ok': file_exists(adv.file_path),
    })


def _range_for(kind):
    """各报告类型的默认区间（用户未指定起止日期时使用）。"""
    today = date.today()
    if kind == 'daily':
        return today, today
    if kind == 'weekly':
        return today - timedelta(days=7), today
    if kind == 'monthly':
        return today.replace(day=1), today
    return today.replace(month=1, day=1), today


def _parse_day(raw):
    """把 YYYY-MM-DD 解析成 date；非法 / 缺失返回 None。"""
    try:
        return date.fromisoformat((raw or '').strip())
    except (ValueError, TypeError):
        return None


def _resolve_range(kind, start_raw, end_raw):
    """把「用户选择的区间」与「报告类型」合成为最终区间。

    - 用户填了完整区间：日报锚定到结束日（日报是单日口径，标题即「该日+日报」），
      其余类型照用；起止填反了自动对调，避免生成空区间。
    - 缺失 / 非法：退回该类型的默认区间（今天 / 近 7 日 / 本月 / 本年），
      保证不带 start/end 的旧调用方式仍然可用。
    """
    start, end = _parse_day(start_raw), _parse_day(end_raw)
    if start and end:
        if start > end:
            start, end = end, start
        return (end, end) if kind == 'daily' else (start, end)
    return _range_for(kind)


def report_gen(request):
    if request.method != 'POST':
        return redirect('/office/')
    kind = (request.POST.get('kind') or '').strip()
    if kind not in KIND_LABELS:        # 白名单：避免伪造类型写入，也让后续取标签不必再判空
        kind = 'daily'
    start, end = _resolve_range(kind, request.POST.get('start'), request.POST.get('end'))
    recs = WorkRecord.objects.filter(date__gte=start, date__lte=end)
    rec_text = '\n'.join(
        f'- [{r.get_category_display()}]{("「" + r.status + "」") if r.status else ""} {r.content}'
        for r in recs
    )
    style = SystemConfig.get_value('user_style', '')
    persona = _persona_block()
    # 同 advise()：persona 已含风格段，避免重复注入
    style_line = f'报告需体现我的个人风格：{style}\n' if (style and not persona) else ''
    prompt = (
        f'请基于以下工作记录，生成一份{KIND_LABELS.get(kind)}（{start} 至 {end}）。\n'
        f'{style_line}'
        f'{persona + chr(10) + chr(10) if persona else ""}'
        f'要求：结构清晰、要点突出、术语规范、可直接用于汇报，并严格遵循上述个人设定与写作风格。\n\n'
        f'工作记录：\n{rec_text or "（本期无工作记录）"}'
    )
    ans = ask_qwen('你是我的公文助理，擅长将工作记录整理为规范、专业的汇报文档。', prompt)
    if not ans:
        ans = (
            f'# {KIND_LABELS.get(kind)}（{start} ~ {end}）\n\n'
            f'> 本地汇总（未配置通义千问 API Key）：\n\n'
            f'{rec_text or "本期暂无工作记录。"}'
        )
    rep = Report.objects.create(kind=kind, content_md=ans, range_start=start, range_end=end)
    # 与献策同一套落盘规则：03_知识库/10_办公平台/报告输出/<报告类型>/<报告名>.md
    path = save_report_file(
        rep.title, rep.get_kind_display(), ans,
        extra_meta=[('报告区间', rep.range_label or ''),
                    ('生成时间', rep.created.strftime('%Y-%m-%d %H:%M') if rep.created else '')],
    )
    if path:
        rep.file_path = path
        rep.save(update_fields=['file_path'])
    log_operation('office', 'report_gen', detail=kind)
    return redirect(f'/office/report/{rep.pk}/')


def report_view(request, pk):
    rep = get_object_or_404(Report, pk=pk)
    html = render_markdown(rep.content_md)
    return render(request, 'office_report.html', {
        'rep': rep, 'html': html,
        'out_path': display_path(rep.file_path),
        'file_ok': file_exists(rep.file_path),
    })


@csrf_exempt
def api_record_update(request):
    """内联编辑工作记录：field ∈ {date, status, category}。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad_json'})
    rec = WorkRecord.objects.filter(pk=data.get('id')).first()
    if not rec:
        return JsonResponse({'ok': False, 'error': 'not_found'})
    field = data.get('field')
    value = (data.get('value') or '').strip()
    if field == 'category':
        if value not in CATEGORY_LABELS:
            return JsonResponse({'ok': False, 'error': 'bad_category'})
        rec.category = value
    elif field == 'status':
        rec.status = value
    elif field == 'date':
        try:
            rec.date = date.fromisoformat(value)
        except (ValueError, TypeError):
            return JsonResponse({'ok': False, 'error': 'bad_date'})
    else:
        return JsonResponse({'ok': False, 'error': 'bad_field'})
    rec.save(update_fields=['category', 'status', 'date'])
    return JsonResponse({
        'ok': True,
        'category': rec.category,
        'category_display': rec.get_category_display(),
        'status': rec.status,
        'date': rec.date.strftime('%Y-%m-%d'),
        'date_label': rec.date.strftime('%m-%d'),
        'weekday': WEEKDAY_CN[rec.date.weekday()],
        'is_today': rec.date == date.today(),
    })


@csrf_exempt
def api_record_delete(request):
    """删除一条工作记录。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad_json'})
    rec = WorkRecord.objects.filter(pk=data.get('id')).first()
    if not rec:
        return JsonResponse({'ok': False, 'error': 'not_found'})
    rec.delete()
    return JsonResponse({'ok': True})


@csrf_exempt
def api_archive_day(request):
    """把某一天的工作记录归档到「工作记录」归档分类（日期卡片上的「归档」按钮调用）。

    参数（JSON）：`date` = YYYY-MM-DD。
    归档范围 = 该日卡片上展示的全部未归档记录，与「工作记录」区的展示口径一致，
    **不受工作文件目录的分类筛选影响**；已归档记录不重复处理。
    归档只改 WorkRecord.category，不产生工作文件、不落磁盘。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'method'})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'bad_json'})
    try:
        day = date.fromisoformat((data.get('date') or '').strip())
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'bad_date'})

    qs = WorkRecord.objects.filter(date=day).exclude(category=ARCHIVE_CAT)
    moved = qs.update(category=ARCHIVE_CAT)
    if moved:
        log_operation('office', 'archive_day', detail='%s x%d' % (day, moved))
    return JsonResponse({'ok': True, 'date': day.strftime('%Y-%m-%d'), 'moved': moved})


@csrf_exempt
def api_search(request):
    """供前端异步调用的全网检索。"""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False})
    q = (data.get('q') or '').strip()
    if not q:
        return JsonResponse({'ok': False})
    results = search_web(q, limit=12)
    return JsonResponse({'ok': True, 'results': results})


# ---------------------------------------------------------------------------
# 工作文件目录：只读展示与调阅（文件由「知识收集 → 本地导入 → 存入办公平台」写入）
# ---------------------------------------------------------------------------
def _workfile_upload_dir():
    """工作文件存放目录（知识库/11_工作文件）：用户上传文件的平台副本所在处。"""
    d = workfile_root()
    os.makedirs(d, exist_ok=True)
    return d


def workfile_view(request, pk):
    """工作文件详情：展示元信息 + 提取内容（Markdown 渲染）；提供只读打开原件入口。"""
    wf = get_object_or_404(WorkFileIndex, pk=pk)
    html = render_markdown(wf.extracted)
    is_image = wf.ext in {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp'}
    return render(request, 'office_workfile.html', {
        'wf': wf, 'html': html, 'is_image': is_image,
        'mtime_str': _fmt_ts(wf.mtime),
        'size_str': _fmt_size(wf.size),
    })


def workfile_open(request, pk):
    """只读调出文件副本：严格限制路径位于工作文件目录内，防越权访问。"""
    wf = get_object_or_404(WorkFileIndex, pk=pk)
    root = os.path.realpath(_workfile_upload_dir())
    real = os.path.realpath(wf.original_path)
    sep = os.sep
    allowed = (real == root or real.startswith(root + sep))
    if not allowed or not os.path.isfile(real):
        return HttpResponseForbidden('无权访问该文件或文件不存在。')
    mime, _ = mimetypes.guess_type(real)
    resp = FileResponse(open(real, 'rb'), filename=os.path.basename(real))
    if mime:
        resp['Content-Type'] = mime
    resp['Content-Disposition'] = 'inline; filename="%s"' % os.path.basename(real)
    return resp


def _fmt_ts(ts):
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def _fmt_size(n):
    try:
        n = int(n)
    except Exception:
        return '—'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return '%.1f %s' % (n, unit)
        n /= 1024
    return '%.1f TB' % n
