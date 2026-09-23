"""工作文件索引内核：把文件以「索引目录」方式登记进办公平台。

设计要点：
- 与知识收集「本地导入」共用同一套解析内核（core.parsers.parse_local_file），不重复造轮子。
- 落库为 WorkFileIndex（索引表）：仅记录 标题 / 路径 / 分类 / 提取文本 / 大小 / 修改时间；
  查看时通过 office.workfile_open 以只读方式调出，**绝不改动磁盘上的原始文件**。
- 唯一入口：知识收集页「本地导入 → 存入办公平台」，按所选工作分类把文件副本归档到
  03_知识库/11_工作文件/<分类>/ 并登记索引。
- 工作分类固定为 行政管理 / 基层党建 / 网络运维（与 office.models.WorkRecord.CATEGORY 一致）。
"""
import os
import logging

from core.models import WorkFileIndex
from core.services import zhi_shi_path

logger = logging.getLogger('kb')

# 与 core.parsers.parse_local_file 支持的格式保持一致（均为「无点」扩展名）
SUPPORTED_EXTS = {
    'md', 'markdown', 'txt', 'docx', 'pdf', 'pptx',
    'jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp',
}

# 办公平台工作分类（与 WorkRecord.CATEGORY 保持一致）
WORKFILE_CATEGORIES = [('admin', '行政管理'), ('party', '基层党建'), ('ops', '网络运维')]
WORKFILE_CATEGORY_LABELS = dict(WORKFILE_CATEGORIES)


def workfile_root():
    """工作文件根目录：知识库/11_工作文件。"""
    return zhi_shi_path('03_知识库', '11_工作文件')


def workfile_dir(category=''):
    """工作文件存放目录；给定分类名时返回其分类子目录（Path）。"""
    root = workfile_root()
    return (root / category) if category else root


def _ext_of(fn):
    """取「无点」小写扩展名（与 SUPPORTED_EXTS 的写法一致）。"""
    return os.path.splitext(fn)[1].lstrip('.').lower()


def index_uploaded_file(original_saved_path, title=None, category='未分类', extracted=''):
    """登记一个已保存到平台的文件到索引表（按路径 upsert，不产生重复记录）。"""
    path = str(original_saved_path)
    ext = _ext_of(os.path.basename(path))
    try:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime
    except Exception:
        size, mtime = 0, 0
    fallback = os.path.splitext(os.path.basename(path))[0]
    entry = WorkFileIndex.objects.filter(original_path=path).first()
    if entry:
        entry.title = title or fallback
        entry.category = category
        entry.ext = ext
        entry.size = size
        entry.mtime = mtime
        entry.extracted = extracted or ''
        entry.save(update_fields=['title', 'category', 'ext', 'size', 'mtime',
                                  'extracted', 'updated'])
        return entry
    return WorkFileIndex.objects.create(
        title=title or fallback, original_path=path, category=category,
        source='upload', ext=ext, size=size, mtime=mtime, extracted=extracted or '')
