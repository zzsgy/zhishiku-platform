"""运行档案：展示工作台全部操作记录（由中间件自动采集）。

每条记录在原始明细之外，附带一行通俗中文解释（_explain 生成），
帮助非技术使用者快速理解该条流水发生了什么。
"""
import csv
import re

from django.shortcuts import render
from django.http import HttpResponse
from django.db.models import Count
from django.utils import timezone

from core.models import OperationLog

# 模块英文名 -> 中文名
MODULE_NAMES = {
    'knowledgebase': '知识库',
    'bookshelf': '书架',
    'collection': '知识收集',
    'office': '办公平台',
    'wiki': 'WiKi 知识页',
    '总览': '平台总览',
    'inspiration': '灵感库',
    'settings': '系统设置',
    'selfmedia': '自媒体工具',
    'starmap': '知识星图',
    'notes': '随笔记',
    'runarchive': '运行档案',
    'link_library': '链接库',
    'build': '平台搭建库（已下线）',
    'dashboard': '仪表盘',
    'api': '开放接口',
}

# 动作代号 -> 中文名
ACTION_NAMES = {
    'view': '打开页面',
    'post': '提交操作',
    'add': '新增记录',
    'delete': '删除记录',
    'golden': '浏览金句',
    'note': '撰写笔记',
    'note_delete': '删除笔记',
    'progress': '更新阅读进度',
    'golden_delete': '删除金句',
    'input_text': '粘贴文本导入',
    'parse_web': '解析网页',
    'import_local': '导入本地文件',
    'import_image': '导入图片',
    'save': '保存设置',
    'status': '修改链接状态',
    'auto_archive': '自动归档链接',
    'retry': '重试解析',
    'create': '新建',
    'edit': '编辑',
    'edit_node': '编辑知识页',
    'work_add': '新增工作事项',
    'report_gen': '生成报告',
    'node_note': '添加知识页笔记',
    'node_note_delete': '删除知识页笔记',
    'node_delete': '删除知识节点',
}

# URL 路径前缀 -> 页面中文名（长前缀优先匹配）
PAGE_NAMES = [
    ('/knowledgebase/links/', '链接库'),
    ('/knowledgebase/golden/', '金句库'),
    ('/runarchive/export/csv/', '运行档案 CSV 导出'),
    ('/runarchive/export/md/', '运行档案 Markdown 导出'),
    ('/collection/link_action/', '链接录入功能'),
    ('/knowledgebase/', '知识库门户'),
    ('/collection/', '知识收集'),
    ('/inspiration/', '灵感库'),
    ('/runarchive/', '运行档案'),
    ('/bookshelf/', '书架'),
    ('/office/', '办公平台'),
    ('/settings/', '系统设置'),
    ('/selfmedia/', '自媒体工具'),
    ('/starmap/', '知识星图'),
    ('/notes/', '随笔记'),
    ('/wiki/', 'WiKi 知识页'),
    ('/build/', '平台搭建库（已下线）'),
    ('/dashboard/', '仪表盘'),
    ('/api/', '开放接口'),
]


def _page_name(path):
    """把请求路径翻译成页面中文名；未收录的路径原样返回。"""
    for prefix, name in PAGE_NAMES:
        if path.startswith(prefix):
            return name
    return path or '页面'


def _explain(log):
    """为一条操作日志生成通俗中文解释（保留原明细，不做删改）。"""
    mod = MODULE_NAMES.get(log.module, log.module)
    act = ACTION_NAMES.get(log.action, log.action)
    detail = (log.detail or '').strip()
    m = re.match(r'^(GET|POST)\s+(\S+)(?:\s*->\s*(\d+))?', detail)
    if m:
        method, path, code = m.groups()
        page = _page_name(path)
        # 页面名已含模块含义时不再重复括注
        suffix = '' if (mod in page or page in mod) else f'（{mod}）'
        if method == 'GET':
            return f'打开了{page}页面{suffix}'
        if code:
            ok = code.startswith('2') or code == '302'
            result = '操作成功' if ok else f'返回状态码 {code}（可能失败）'
            s = f'在{page}{suffix}提交了一次操作'
            if act != '提交操作':
                s += f'（{act}）'
            return s + f'，{result}'
        s = f'在{page}{suffix}提交了一次操作'
        if act != '提交操作':
            s += f'（{act}）'
        return s
    if detail:
        return f'在{mod}进行了「{act}」：{detail[:60]}'
    return f'在{mod}进行了「{act}」'


def _filter_logs(request):
    """按当前 module / q 过滤操作日志（导出与列表共用）。"""
    module = (request.GET.get('module') or '').strip()
    q = (request.GET.get('q') or '').strip()
    logs = OperationLog.objects.all()
    if module:
        logs = logs.filter(module=module)
    if q:
        logs = logs.filter(detail__icontains=q)
    return logs


from core.pagination import paginate

def index(request):
    page = paginate(request, _filter_logs(request).order_by("-created", "-pk"), 50)
    logs = page
    modules = [
        {'module': m['module'], 'c': m['c'],
         'label': MODULE_NAMES.get(m['module'], m['module'])}
        for m in OperationLog.objects.values('module').annotate(c=Count('id')).order_by('-c')
    ]
    for l in logs:
        l.explain = _explain(l)
    return render(request, 'runarchive.html', {
        'logs': logs, 'modules': modules, 'page_obj': page,
        'module': (request.GET.get('module') or '').strip(),
        'q': (request.GET.get('q') or '').strip(),
    })


class Echo:
    def write(self, value):
        return value


def export_csv(request):
    from django.http import StreamingHttpResponse
    writer = csv.writer(Echo())
    def rows():
        yield '\ufeff'
        yield writer.writerow(['时间', '模块', '动作', '明细', '说明'])
        for log in _filter_logs(request).iterator(chunk_size=500):
            yield writer.writerow([log.created.strftime('%Y-%m-%d %H:%M:%S'), log.module,
                                   log.action, log.detail, _explain(log)])
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="runarchive.csv"'
    return response


def export_md(request):
    from django.http import StreamingHttpResponse
    def rows():
        yield '# 运行档案导出\n\n| 时间 | 模块 | 动作 | 明细 | 说明 |\n| --- | --- | --- | --- | --- |\n'
        for log in _filter_logs(request).iterator(chunk_size=500):
            values = [log.created.strftime('%Y-%m-%d %H:%M:%S'), log.module, log.action, log.detail, _explain(log)]
            yield '| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ').replace('\r', '') for v in values) + ' |\n'
    response = StreamingHttpResponse(rows(), content_type='text/markdown; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="runarchive.md"'
    return response
