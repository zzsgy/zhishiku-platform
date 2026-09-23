"""知识沉淀核心模块：来源解析、原文删除容错、AI 提炼与自生长。

设计要点（对照「知识沉淀机制」约束）：
1. 原始资料只读：本模块只「读」原始文档（KnowledgeNode.content_md / 办公平台文件），
   绝不对原始节点或文件做任何写回；提炼产物是**新**的 KnowledgeNode。
2. 来源解耦：来源仅以 (origin_type, origin_ref) 字符串记录，不建外键，
   因此原始文档被删时，本节点不受影响，仅打开来源时提示「原文已删除」。
3. 来源类型开放注册（ORIGIN_REGISTRY）：后期新增的办公平台产物类型，
   只需在此登记一个 resolver，即可被沉淀机制覆盖（满足「含后期新增功能生成的文件」）。
"""
import json
import logging
import os
import re

from core.ai import ask_ai
from core.models import KnowledgeNode, WorkFileIndex
from core.services import (
    auto_link_edges, sync_node_to_file, default_base, log_operation,
)

logger = logging.getLogger('kb')

# 来源类型注册表：新增办公平台产物类型时，在此追加一项即可被沉淀机制识别。
# 每项提供 label（展示名）与 resolve(ref) -> {exists,label,url}（原文删除检测）。
ORIGIN_TYPES = {
    'kb_node': '知识库节点',
    'office_advise': '办公平台·献策',
    'office_report': '办公平台·报告',
    'office_workfile': '办公平台·工作文件',
    'multi': '多来源合并',
}


# ---------------------------------------------------------------------------
# 来源解析（含原文删除容错）
# ---------------------------------------------------------------------------
def _resolve_kb_node(ref):
    n = KnowledgeNode.objects.filter(pk=ref).first()
    if n:
        return {'exists': True, 'label': n.title, 'url': '/knowledgebase/node/%s/' % n.pk}
    return {'exists': False, 'label': '', 'url': ''}


def _resolve_office(ref, model_name, url_tpl):
    try:
        from office.models import Advise, Report
        model = {'advise': Advise, 'report': Report}[model_name]
    except Exception:
        return {'exists': False, 'label': '', 'url': ''}
    obj = model.objects.filter(pk=ref).first()
    if obj:
        title = getattr(obj, 'title', '') or ('#%s' % ref)
        return {'exists': True, 'label': title, 'url': url_tpl % obj.pk}
    return {'exists': False, 'label': '', 'url': ''}


def resolve_one(origin_type, ref):
    """解析单一来源；返回 {exists, label, url}。删除或未知 => exists=False。"""
    ref = (ref or '').strip()
    if origin_type == 'kb_node':
        return _resolve_kb_node(ref)
    if origin_type == 'office_advise':
        return _resolve_office(ref, 'advise', '/office/advise/%s/')
    if origin_type == 'office_report':
        return _resolve_office(ref, 'report', '/office/report/%s/')
    if origin_type == 'office_workfile':
        w = WorkFileIndex.objects.filter(pk=ref).first()
        if w:
            return {'exists': True, 'label': w.title, 'url': '/office/workfile/%s/' % w.pk}
        return {'exists': False, 'label': '', 'url': ''}
    return {'exists': False, 'label': '', 'url': ''}


def resolve_origin(origin_type, origin_ref):
    """统一入口：返回 {exists, label, url, deleted, origins}。

    - 单来源：origins=[该来源]，deleted 视 exists 而定。
    - multi：origins 为各来源解析结果列表；任一处原文被删即 deleted=True。
    """
    info = {'exists': False, 'label': '', 'url': '', 'deleted': True, 'origins': []}
    if origin_type == 'multi':
        try:
            lst = json.loads(origin_ref or '[]')
        except Exception:
            lst = []
        origins = []
        for d in lst:
            o = resolve_one((d or {}).get('type'), str((d or {}).get('ref')))
            origins.append(o)
        info['origins'] = origins
        info['exists'] = bool(origins) and all(o['exists'] for o in origins)
        info['label'] = '%d 个来源' % len(origins)
        info['deleted'] = not info['exists']
        return info
    o = resolve_one(origin_type, origin_ref)
    info.update(o)
    info['origins'] = [o]
    info['deleted'] = not o['exists']
    return info


# ---------------------------------------------------------------------------
# 源内容读取（只读，不写入原始文档）
# ---------------------------------------------------------------------------
def get_source_title(origin_type, origin_ref):
    if origin_type == 'multi':
        return resolve_origin('multi', origin_ref).get('label', '')
    return resolve_one(origin_type, origin_ref).get('label', '')


def get_source_content(origin_type, origin_ref):
    """读取原始文档正文（只读）。失败返回空串，不抛异常。"""
    try:
        if origin_type == 'kb_node':
            n = KnowledgeNode.objects.filter(pk=origin_ref).first()
            return n.content_md or '' if n else ''
        if origin_type in ('office_advise', 'office_report'):
            try:
                from office.models import Advise, Report
                model = Advise if origin_type == 'office_advise' else Report
            except Exception:
                return ''
            obj = model.objects.filter(pk=origin_ref).first()
            if not obj:
                return ''
            fp = getattr(obj, 'file_path', '') or ''
            if fp and os.path.isfile(fp):
                try:
                    return open(fp, encoding='utf-8', errors='ignore').read()
                except Exception:
                    pass
            return getattr(obj, 'content_md', '') or ''
        if origin_type == 'office_workfile':
            w = WorkFileIndex.objects.filter(pk=origin_ref).first()
            if not w:
                return ''
            if w.extracted:
                return w.extracted
            fp = getattr(w, 'original_path', '') or ''
            if fp and os.path.isfile(fp):
                try:
                    return open(fp, encoding='utf-8', errors='ignore').read()
                except Exception:
                    pass
            return ''
    except Exception as e:  # pragma: no cover
        logger.error('get_source_content failed: %s', e)
    return ''


# ---------------------------------------------------------------------------
# AI 提炼：把原始文档核心信息抽取为一篇知识卡片（含自动双链）
# ---------------------------------------------------------------------------
_EXTRACT_SYSTEM = (
    '你是一个个人知识库编辑。请阅读下方原始文档（可能有多篇，以分隔线区分），'
    '提炼核心信息，形成一篇结构化的知识卡片。\n'
    '只输出一个 JSON 对象，不要附加任何解释，格式：\n'
    '{"title":"提炼后的知识标题","category":"分类名","tags":"逗号分隔的标签",'
    '"content_md":"Markdown 正文"}\n'
    '要求：content_md 用中文，保留关键事实、结论与要点；'
    '在正文中用 [[已有知识标题]] 的方式关联你判断已经存在于知识库中的相关知识'
    '（仅当标题确实可能已存在时才使用双链，不要编造不存在的链接目标）；'
    'category 优先从已有分类中选择，必要时可新建；tags 控制在 2-5 个。'
)


def parse_ai_json(text, required_key='content_md'):
    """容错解析 AI 返回的 JSON：剥离 ```json 围栏，定位首个 { ... } 末个 }。

    required_key: 必须包含的顶层字段。提炼场景要求 content_md；
    自生长场景传 None（任意 JSON 对象即可，其结构由调用方校验）。
    """
    if not text:
        return None
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.S)
    if m:
        t = m.group(1)
    else:
        s = t.find('{')
        e = t.rfind('}')
        if s >= 0 and e > s:
            t = t[s:e + 1]
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if isinstance(obj, dict) and (not required_key or required_key in obj):
        if required_key == 'content_md':
            obj.setdefault('title', '提炼知识')
            obj.setdefault('category', '知识沉淀')
            obj.setdefault('tags', '')
        return obj
    return None


def _fallback_extract(sources):
    """AI 不可用时的本地降级：生成占位草稿，保证流程不中断。"""
    titles = [get_source_title(t, r) for t, r in sources]
    title = (titles[0] or '提炼草稿')
    joined = []
    for i, (t, r) in enumerate(sources, 1):
        c = get_source_content(t, r) or ''
        joined.append('（来源 %d）%s' % (i, c[:1200]))
    return {
        'title': '%s（提炼草稿）' % title,
        'category': '知识沉淀',
        'tags': 'AI提炼',
        'content_md': (
            '# %s\n\n> 来源：%s\n\n'
            '（AI 服务当前不可用，已生成本地降级草稿，请在 WiKI 层编辑完善，'
            '并手动补全 [[双链]] 关联已有知识。）\n\n%s'
            % (title, '、'.join(titles), '\n\n'.join(joined))
        ),
    }


def extract_knowledge(sources, provider=None):
    """sources: [(origin_type, origin_ref), ...]。返回 {title,category,tags,content_md}。"""
    sources = [(t, str(r)) for t, r in sources if t in ORIGIN_TYPES and str(r).strip()]
    if not sources:
        return None
    parts = []
    for i, (t, r) in enumerate(sources, 1):
        title = get_source_title(t, r) or ('来源%d' % i)
        c = get_source_content(t, r) or '(无正文)'
        parts.append('=== 来源 %d：%s ===\n%s' % (i, title, c[:6000]))
    user = '\n\n'.join(parts)
    raw = ask_ai(_EXTRACT_SYSTEM, user, provider_id=provider, temperature=0.5)
    data = parse_ai_json(raw) if raw else None
    if not data:
        logger.warning('AI 提炼失败/不可用，启用本地降级草稿')
        return _fallback_extract(sources)
    return data


# ---------------------------------------------------------------------------
# AI 自生长：对已存在的 WiKI 知识节点做分类 / 定义 / 补双链
# ---------------------------------------------------------------------------
_GROW_SYSTEM = (
    '你是个人知识库编辑。请阅读下方知识正文，完成三件事并只输出一个 JSON：\n'
    '{"category":"更合适的分类","tags":"逗号分隔标签",'
    '"definition":"对该知识的术语定义(2-4 句中文)",'
    '"extra_links":["可能已存在的知识标题",...]}\n'
    'extra_links 仅填你判断已存在于知识库、且正文尚未关联的标题；'
    '不要编造链接目标。不要附加解释。'
)


def grow_node(node, provider=None):
    """对单个节点调用 AI 自生长，返回 {category,tags,definition,extra_links} 或 None。"""
    user = '知识标题：%s\n分类：%s\n标签：%s\n\n正文：\n%s' % (
        node.title, node.category, node.tags, (node.content_md or '')[:5000])
    raw = ask_ai(_GROW_SYSTEM, user, provider_id=provider, temperature=0.4)
    # 自生长返回结构不含 content_md，解析时不强制该字段
    data = parse_ai_json(raw, required_key=None) if raw else None
    if not data:
        return None
    data.pop('content_md', None)  # 自生长不覆盖正文，只允许补齐
    return data


# ---------------------------------------------------------------------------
# 节点落库辅助：采纳（写文件 + 建边）、创建提炼草稿
# ---------------------------------------------------------------------------
def create_precip_node(sources, data, provider=None):
    """依据 sources 与 AI 产出 data 创建一条「待处理」知识节点。

    返回新建的 KnowledgeNode。来源：单来源直接记录；多来源 origin_type='multi'。
    """
    if not data:
        return None
    # 主来源（用于 base / 主 origin）
    primary = sources[0]
    base = None
    if primary[0] == 'kb_node':
        src = KnowledgeNode.objects.filter(pk=primary[1]).first()
        base = src.base if src else None
    base = base or default_base('knowledge')

    if len(sources) > 1:
        origin_type = 'multi'
        origin_ref = json.dumps(
            [{'type': t, 'ref': str(r)} for t, r in sources], ensure_ascii=False)
        origin_title = '%d 个来源' % len(sources)
    else:
        origin_type = primary[0]
        origin_ref = str(primary[1])
        origin_title = get_source_title(primary[0], primary[1])

    title = (data.get('title') or '未命名提炼').strip() or '未命名提炼'
    node = KnowledgeNode.objects.create(
        base=base,
        title=title,
        content_md=data.get('content_md', ''),
        node_type='wiki',
        category=(data.get('category') or '').strip(),
        tags=(data.get('tags') or '').strip(),
        status='pending',
        origin_type=origin_type,
        origin_ref=origin_ref,
        origin_title=origin_title,
        ai_generated=True,
    )
    # 自生长第一步：把正文里的 [[双链]] 即时落成关系边（与正式节点一致）
    auto_link_edges(node)
    log_operation('wiki', 'precip_create', detail=node.title)
    return node


def adopt_node(node):
    """采纳：转正式、写文件、建边。"""
    node.status = 'adopted'
    node.save(update_fields=['status', 'updated'])
    sync_node_to_file(node)
    auto_link_edges(node)
    log_operation('wiki', 'precip_adopt', detail=node.title)
    return node


def discard_node(node):
    """丢弃：删除草稿及其关系边（不影响任何原始文档）。"""
    title = node.title
    node.delete()  # 级联删除 edges / notes
    log_operation('wiki', 'precip_discard', detail=title)
