"""办公平台产出物的落盘规则（唯一实现）。

办公平台生成的内容统一下沉到知识库「10_办公平台」，再按**产物类型 + 工作分类**分目录：

    C:\\ZSK\\ZhiShi\\03_知识库\\10_办公平台\\
        ├─ 献策输出\\<工作分类>\\<YYYY.MM.DD>_<主题>.md
        └─ 报告输出\\<报告类型>\\<报告名>.md

设计约定：
- 目录名用**中文**（工作分类 = 行政管理 / 基层党建 / 网络运维；报告类型 = 日报 / 周报 /
  月报 / 年报），与既有的「11_工作文件\\<分类>\\」保持一致，便于在 Obsidian 里对照翻阅。
- 文件名统一经 `sanitize_filename` 清洗，规避 Windows 非法字符（\\ / : * ? " < > |）。
- 落盘失败只写日志、不抛异常、不阻断主流程：数据库记录（Advise / Report）才是权威索引，
  前端的列表与统一搜索都基于数据库；磁盘文件用于人工翻阅与归档。
"""
import logging
import os
from datetime import date
from pathlib import Path

from core.services import ensure_dir, sanitize_filename, zhi_shi_path

logger = logging.getLogger('kb')

# 办公平台产出物根目录（相对知识库根）
_OFFICE_PARTS = ('03_知识库', '10_办公平台')
ADVISE_SUBDIR = '献策输出'
REPORT_SUBDIR = '报告输出'


def office_output_root():
    """办公平台产出物根目录：知识库/10_办公平台。"""
    return zhi_shi_path(*_OFFICE_PARTS)


def advise_dir(category_label=''):
    """献策输出目录；给定工作分类名时返回其子目录（Path，未必已存在）。"""
    root = office_output_root() / ADVISE_SUBDIR
    return (root / category_label) if category_label else root


def report_dir(kind_label=''):
    """报告输出目录；给定报告类型名时返回其子目录（Path，未必已存在）。"""
    root = office_output_root() / REPORT_SUBDIR
    return (root / kind_label) if kind_label else root


def output_name(prefix, subject, maxlen=60):
    """`前缀_主题.md`——前缀通常是日期（YYYY.MM.DD）或报告名。"""
    return '%s_%s.md' % (prefix, sanitize_filename(subject, maxlen))


def _write(dirpath, filename, content):
    """写文本文件并返回绝对路径；失败返回空串（仅记日志，不影响主流程）。"""
    try:
        ensure_dir(dirpath)
        path = Path(dirpath) / filename
        path.write_text(content or '', encoding='utf-8')
        return str(path)
    except Exception as e:  # pragma: no cover - 权限/占用等环境问题
        logger.error('办公平台产出落盘失败 %s / %s: %s', dirpath, filename, e)
        return ''


def _front_matter(title, meta_lines):
    """统一的文件头（Markdown 引用块），让落盘文件脱离数据库也能自描述。"""
    lines = ['# %s' % title, '']
    for k, v in meta_lines:
        if v:
            lines.append('> %s：%s  ' % (k, v))
    lines.append('')
    return '\n'.join(lines) + '\n'


def save_advise_file(topic, category_label, content_md, day=None, source_label=''):
    """保存一份献策 Markdown，返回文件绝对路径（失败返回 ''）。

    路径：03_知识库/10_办公平台/献策输出/<工作分类>/<YYYY.MM.DD>_<主题>.md
    """
    day = day or date.today()
    name = output_name(day.strftime('%Y.%m.%d'), topic or '未命名主题')
    head = _front_matter(topic or '未命名主题', [
        ('类型', 'AI 献策'),
        ('工作分类', category_label),
        ('生成方式', source_label),
        ('生成日期', day.strftime('%Y-%m-%d')),
    ])
    return _write(advise_dir(category_label), name, head + (content_md or ''))


def save_report_file(title, kind_label, content_md, extra_meta=()):
    """保存一份报告 Markdown，返回文件绝对路径（失败返回 ''）。

    路径：03_知识库/10_办公平台/报告输出/<报告类型>/<报告名>.md
    """
    title = title or '未生成命名报告'
    name = '%s.md' % sanitize_filename(title, 80)
    head = _front_matter(title, [('类型', kind_label)] + [m for m in extra_meta])
    return _write(report_dir(kind_label), name, head + (content_md or ''))


def display_path(path):
    """把绝对路径裁剪成「03_知识库\\...」形式，供前端展示（对用户更可读）。"""
    if not path:
        return ''
    p = str(path).replace('/', '\\')
    marker = '\\03_知识库\\'
    i = p.find(marker)
    return ('03_知识库\\' + p[i + len(marker):]) if i >= 0 else p


def file_exists(path):
    try:
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False
