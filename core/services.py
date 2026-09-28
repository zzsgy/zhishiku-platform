"""核心服务层：操作日志、目录映射、Markdown 渲染与双链、知识库文件同步。

所有模块共用，避免重复代码。文件落盘遵循约定：
  C:/ZSK/ZhiShi/03_知识库/...   原始资料与生成内容
"""
import re
import logging
from pathlib import Path

from django.conf import settings
from .models import (
    KnowledgeBase, KnowledgeNode, OperationLog, CollectionItem, LinkItem,
)

logger = logging.getLogger('kb')

# ---------------------------------------------------------------------------
# 操作日志（运行档案模块使用）
# ---------------------------------------------------------------------------
def log_operation(module, action, detail='', user='系统'):
    """记录一次工作台操作。失败不影响主流程。"""
    try:
        OperationLog.objects.create(module=module, action=action, detail=detail, user=user)
    except Exception as e:  # pragma: no cover
        logger.error('log_operation failed: %s', e)


# ---------------------------------------------------------------------------
# 目录映射（与 settings.ZHI_SHI_DIRS 对应）
# ---------------------------------------------------------------------------
def zhi_shi_path(*parts):
    return Path(settings.ZHI_SHI_ROOT, *parts)


def ensure_dir(path):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_base(kind='knowledge'):
    return KnowledgeBase.objects.filter(kind=kind).first()


# ---------------------------------------------------------------------------
# Markdown 渲染 + [[双链]] 处理（Obsidian 风格）
# ---------------------------------------------------------------------------
_WIKILINK_RE = re.compile(r'\[\[([^\]]+?)(?:\|([^\]]+?))?\]\]')


def render_markdown(text):
    """把 Markdown 渲染为 HTML，并把 [[标题|显示]] 转为站内双链。"""
    if not text:
        return ''
    try:
        from markdown import markdown as md
        html = md(text, extensions=['extra', 'sane_lists', 'tables', 'fenced_code', 'toc'])
    except Exception:
        try:
            from markdown import markdown as md
            html = md(text)
        except Exception:
            html = f'<pre>{text}</pre>'
    html = _apply_wikilinks_outside_code(html)
    html = wrap_tables(html)
    return html


_TABLE_RE = re.compile(r'(<table[\s\S]*?</table>)', re.IGNORECASE)


def wrap_tables(html):
    """给每个表格套一层横向滚动容器 .kb-table-wrap。

    背景：正文列宽约 769px，而「长路径 + 长摘要」这类多列表格的 min-content
    可达 1182px。若让表格直接撑开，它会溢出正文卡片并侵入右栏，被吸顶的批注台
    白底盖住 —— 表现为「表格文字跑出表格边界」。套上滚动容器后，表格要么按
    容器宽度正常排版，要么在容器内部横向滚动，永远不会越界压到批注台。
    """
    if not html or '<table' not in html:
        return html
    return _TABLE_RE.sub(r'<div class="kb-table-wrap">\1</div>', html)


def _apply_wikilinks_outside_code(html):
    """仅在非代码块（<pre>/<code>）之外替换 [[双链]]，避免把代码里的 [[...]] 误链接化。"""
    parts = re.split(r'(<pre[\s\S]*?</pre>|<code[\s\S]*?</code>)', html)
    for i in range(0, len(parts), 2):  # 偶数下标为非代码块文本
        parts[i] = _WIKILINK_RE.sub(_wikilink_repl, parts[i])
    return ''.join(parts)


def _wikilink_repl(m):
    """[[标题]] / [[标题|别名]] → 站内双链。

    只输出链接文字，不再把 [[ ]] 画出来：方括号是「书写语法」，只属于 Markdown 源码；
    阅读页与编辑页都应呈现成普通链接（蓝色 + 虚线下划线 + 悬停提示目标页）。
    这样编辑器才能做到「编辑态不暴露语法符号」，且与阅读页完全一致。
    """
    title = m.group(1).strip()
    label = (m.group(2) or title).strip()
    from urllib.parse import quote
    return (f'<a class="kb-wikilink" href="/wiki/?title={quote(title)}" '
            f'title="打开知识页：{title}">{label}</a>')


def extract_wikilinks(text):
    """提取正文中所有 [[双链]] 指向的标题。"""
    out = []
    for m in _WIKILINK_RE.finditer(text or ''):
        t = m.group(1).strip()
        if t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# 知识节点 <-> Markdown 文件 双向同步
# ---------------------------------------------------------------------------
def sanitize_filename(name, maxlen=80):
    name = (name or 'untitled').strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name)
    return name[:maxlen] or 'untitled'


# 知识点 category -> 03_知识库 下的子目录（与目录规划一致）
CATEGORY_FOLDER = {
    '本地导入': '01_本地导入',
    '网页解析': '02_网页解析',
    '视频转图文': '03_视频转图文',
    '文本输入': '04_文本输入',
    'AI生成': '05_AI生成',
    '书籍': '06_书籍',
    '灵感库': '07_灵感库',
    'WIKI': '08_WIKI',
    '自媒体': '09_自媒体',
    '办公平台': '10_办公平台',
    '工作文件': '11_工作文件',
}

# 桌面归档：按主题细分到子目录，保持来源层级，便于按主题检索。
ARCHIVE_CATEGORY_FOLDER = {
    '归档·党建党务': '11_工作文件/党建党务',
    '归档·档案管理': '11_工作文件/档案管理',
    '归档·IT运维与信息化': '11_工作文件/IT运维与信息化',
    '归档·质量管理与GMP': '11_工作文件/质量管理与GMP',
    '归档·行政人事与内控': '11_工作文件/行政人事与内控',
    '归档·系统开发与平台建设': '11_工作文件/系统开发与平台建设',
    '归档·个人申报与职称': '11_工作文件/个人申报与职称',
    '归档·企业与产品资料': '11_工作文件/企业与产品资料',
    '归档·学习与考试': '06_书籍/学习与考试',
    '归档·自媒体运营': '09_自媒体/运营资料',
    '归档·AI工具与数字工作台': '05_AI生成/AI工具与数字工作台',
    '归档·索引': '11_工作文件/00_归档索引',
}
CATEGORY_FOLDER.update(ARCHIVE_CATEGORY_FOLDER)


def node_markdown_path(node):
    if not node.base or not node.base.directory:
        return None
    base_dir = zhi_shi_path(node.base.directory)
    folder = CATEGORY_FOLDER.get(node.category)
    if folder:
        base_dir = base_dir / folder
    ensure_dir(base_dir)
    return base_dir / f'{sanitize_filename(node.title)}.md'


def sync_node_to_file(node):
    path = node_markdown_path(node)
    if not path:
        return None
    try:
        content = f'# {node.title}\n\n> 类型：{node.get_node_type_display()}  '
        if node.category:
            content += f'分类：{node.category}  '
        if node.tags:
            content += f'标签：{node.tags}  '
        if node.source:
            content += f'\n> 来源：{node.source}'
        content += f'\n\n{node.content_md or ""}'
        path.write_text(content, encoding='utf-8')
        return str(path)
    except Exception as e:
        logger.error('sync_node_to_file failed: %s', e)
        return None


def auto_link_edges(node):
    """根据正文里的 [[标题]] 自动建立关系边，并清理旧的 out 链接。"""
    from .models import Edge
    Edge.objects.filter(source=node, kind='link').delete()
    for t in extract_wikilinks(node.content_md):
        target = KnowledgeNode.objects.filter(title__iexact=t).exclude(pk=node.pk).first()
        if target:
            Edge.objects.get_or_create(source=node, target=target, kind='link')


def link_collection_to_node(item: CollectionItem, base=None, title=None, category=''):
    """把一条收集项落成知识节点（Markdown 形式直接进入知识库）。

    上传解析后的内容直接入库为 adopted 节点：进入知识库列表 / WiKI 树 / 星图，
    并按正文 [[双链]] 即时建边。知识沉淀（AI 提炼）用于对知识库与办公平台
    产物做二次加工产出 Wiki 层知识，不作为上传入库的必经环节。
    """
    base = base or default_base('knowledge')
    node = KnowledgeNode.objects.create(
        base=base,
        title=title or item.title or f'收集-{item.pk}',
        content_md=item.parsed_md or item.raw_text,
        node_type='article',
        category=category or item.kind,
        source=item.source_url or '本地收集',
        status='adopted',
    )
    sync_node_to_file(node)
    # 收集导入的网页/OCR 文档常含 [[双链]]，落库后即时建边，纳入星图与反向链接
    auto_link_edges(node)
    return node


# ---------------------------------------------------------------------------
# 链接库写入（知识库链接库 + 知识收集模块共用，保证存储逻辑唯一）
# ---------------------------------------------------------------------------
def add_link_to_library(url, title='', source='manual', link_type='unknown',
                        unparse_reason='unknown', status='archived', tags='',
                        note='', collector='系统', related_base=None):
    """把一条链接写入链接库（唯一存储逻辑）。

    供知识库「链接库」手动添加、知识收集「录入链接库 / 解析失败自动归档」
    共用，确保存储行为一致。
    状态约定：用户直接存入的链接默认 status='archived'（链接归档）；
    解析失败自动归档的链接由调用方显式传 status='pending'（待处理），
    待用户在链接库点击「链接入库」后再转为 archived。
    返回新建的 LinkItem；url 为空或已存在时抛出 ValueError，由调用方给出反馈。
    """
    url = (url or '').strip()
    if not url:
        raise ValueError('链接不能为空')
    if LinkItem.objects.filter(url=url).exists():
        raise ValueError('该链接已存在于链接库')
    obj = LinkItem(
        url=url,
        title=(title or '').strip(),
        source=source,
        link_type=link_type,
        unparse_reason=unparse_reason,
        status=status,
        tags=(tags or '').strip(),
        note=(note or '').strip(),
        collector=collector,
    )
    if related_base is not None:
        try:
            obj.related_base_id = int(related_base)
        except (TypeError, ValueError):
            pass
    obj.save()
    log_operation('link_library', 'add', detail=url)
    return obj
