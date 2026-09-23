"""三类备份的范围规范（唯一事实源）。

三个诉求 -> 三个 scope：

  1. knowledge 知识备份
     范围 = 平台产生 + 用户上传/新建的**全部知识资料**，不含平台程序本身。
     覆盖：知识库 / WiKI / 知识星图关系 / 书架 / 办公平台 / 自媒体 / 灵感库 /
           知识收集 / 链接库 / 运行档案流水，以及它们在磁盘上的正文与上传媒体。

  2. system 系统备份（空运行平台）
     范围 = 平台跑起来所需的**程序与依赖**，零知识数据。
     覆盖：全部 Python 源码与模板 / 静态资源 / 依赖清单 / 外部工具 / 一键安装脚本 /
           空知识库骨架目录。交给他人即可一键装出同款空平台。

  3. migration 迁移备份
     范围 = knowledge ∪ system。换机时整机搬迁：恢复平台运行 + 全部知识资料。

本文件是三者差异的**唯一事实源**：
  * 引擎（backup_engine.py）按 `srcs` / `db_dump` / `embed_db` 收集内容；
  * 前端（settings.html）按 `includes` / `excludes` / `purpose` 渲染范围说明；
  * 验收脚本按同一份定义做断言。
调整范围只改这里，不要在其他地方硬写第二份清单。
"""
from pathlib import Path

from django.conf import settings

# ---------------------------------------------------------------------------
# 三个根目录
#   CODE_ROOT   平台代码     <root>/XiTong   （本文件所在工程）
#   DATA_ROOT   知识数据     <root>/ZhiShi   （可与代码同级的任意位置）
#   BACKUP_ROOT 备份产物输出 <root>/备份输出（刻意在代码目录之外，避免被下次备份递归打包）
# ---------------------------------------------------------------------------
CODE_ROOT = Path(settings.BASE_DIR)
DATA_ROOT = Path(settings.ZHI_SHI_ROOT)
BACKUP_ROOT = Path(settings.BACKUP_ROOT)

# ---------------------------------------------------------------------------
# 数据库表分组
# ---------------------------------------------------------------------------
# 知识数据表：平台产生 / 用户录入的全部知识记录。book / note / golden 等
# “笔记型”数据也在其中——它们同样是用户产出，不能丢。
KNOWLEDGE_MODELS = [
    'core.KnowledgeBase',     # 知识库分库
    'core.KnowledgeNode',     # 知识节点（知识库 + WiKI + 自媒体草稿）
    'core.NodeNote',          # 节点笔记
    'core.Edge',              # 关系边（知识星图 / 双向链接）
    'core.CollectionItem',    # 知识收集记录
    'core.LinkItem',          # 链接库
    'core.WorkFileIndex',     # 工作文件索引
    'core.OperationLog',      # 运行档案流水
    'bookshelf.Book',         # 书籍
    'bookshelf.ReadingNote',  # 读书笔记
    'bookshelf.GoldenSentence',  # 金句库
    'inspiration.Inspiration',   # 灵感库
    'inspiration.NoteLog',       # 随笔记历史
    'office.WorkRecord',      # 办公平台·工作记录
    'office.Advise',          # 办公平台·AI 献策
    'office.Report',          # 办公平台·报告
]

# 系统配置表：只有迁移包才带。知识包刻意不带——它含 AI 服务的 API Key，
# 不该跟着知识资料一起外发。
SYSTEM_MODELS = ['core.SystemConfig']

# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------
# 代码目录内一律排除的目录名（可重建 / 机器相关 / 运行期产物）。
CODE_EXCLUDE_DIRS = {
    'venv', 'env', '.venv',                 # 依赖环境：安装脚本重建（含绝对路径，跨机不可用）
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.git', '.svn', '.hg', '.idea', '.vscode',
    'node_modules',                         # 前端依赖：由 npm/vite 重建
    'static_collected',                     # collectstatic 产物
    'logs',                                 # 运行日志
    'media',                                # 上传媒体原件：属知识资料（知识包/迁移包带走）；
                                            # 系统包必须清空，只由骨架补空目录
    'backups',                              # 历史备份
    'ffmpeg_tmp',                           # 外部工具的解压临时目录
}

# 代码目录内一律排除的文件名。
CODE_EXCLUDE_FILES = {
    '.DS_Store', 'Thumbs.db', 'desktop.ini',
    '_tmp_last.txt', '_z.txt',
    'probe_out.txt', 'repro_out.txt', 'server_run.log',
}

# 代码目录内一律排除的文件后缀（压缩包/缓存/备份/日志）。
CODE_EXCLUDE_SUFFIXES = (
    '.pyc', '.pyo', '.pyd',
    '.log', '.tmp', '.bak', '.old', '.orig', '.swp',
    '.sqlite3-journal', '.sqlite3-wal', '.sqlite3-shm',
    '.zip', '.7z', '.rar', '.tar', '.gz',
)

# 代码目录内一律排除的文件名前缀（本机诊断脚本产物）。
CODE_EXCLUDE_PREFIXES = ('_diag', '_probe', '_repro', '_check')

# 仅「系统包」排除：运行数据库与上传媒体。
#   系统包的目标是「空运行平台」，知识数据的载体必须为空——
#   数据库由安装脚本 migrate + init_config 新建，媒体目录留空即可。
SYSTEM_ONLY_EXCLUDE_FILES = {'db.sqlite3'}


# ---------------------------------------------------------------------------
# 知识库骨架（系统包/迁移包随包附带，保证解压后 DATA_ROOT 一定存在）
# ---------------------------------------------------------------------------
def zhi_shi_skeleton_dirs():
    """返回知识库应存在的相对目录列表（相对 DATA_ROOT）。"""
    return [
        '02_运行档案库',
        '03_知识库/01_本地导入/images',
        '03_知识库/01_本地导入/markdown',
        '03_知识库/01_本地导入/pdf',
        '03_知识库/01_本地导入/ppt',
        '03_知识库/01_本地导入/txt',
        '03_知识库/01_本地导入/word',
        '03_知识库/02_网页解析',
        '03_知识库/03_视频转图文',
        '03_知识库/04_文本输入',
        '03_知识库/05_AI生成',
        '03_知识库/06_书籍/covers',
        '03_知识库/06_书籍/ebooks',
        '03_知识库/06_书籍/golden_sentences',
        '03_知识库/06_书籍/notes',
        '03_知识库/07_灵感库',
        '03_知识库/08_WIKI',
        '03_知识库/09_自媒体',
        '03_知识库/10_办公平台/献策输出',
        '03_知识库/10_办公平台/报告输出',
        '03_知识库/11_工作文件/行政管理',
        '03_知识库/11_工作文件/基层党建',
        '03_知识库/11_工作文件/网络运维',
        '04_临时中转',
        '05_归档库',
    ]


# ---------------------------------------------------------------------------
# 三类范围定义
# ---------------------------------------------------------------------------
SCOPES = {
    'knowledge': {
        'kind': 'knowledge',
        'title': '知识备份',
        'subtitle': '备份所有知识资料',
        'icon': 'bi-journal-arrow-down',
        'tone': 'warning',
        'badge': '只含知识 · 不含程序',
        'purpose': '把「平台上产生 + 我上传/新建」的全部知识资料单独打包留存。'
                   '换电脑、系统重装、误删恢复时，用它把知识内容找回来即可，'
                   '不必连程序一起搬。',
        'includes': [
            '知识库正文：本地导入 / 网页解析 / 视频转图文 / 文本输入 / AI 生成',
            'WiKI 层：Markdown 正文 + [[双链]] 关系（知识星图用到的关系边）',
            '书架：书籍信息 / 阅读进度 / 读书笔记 / 金句库',
            '办公平台：工作记录 / AI 献策 / 日报周报月报年报',
            '自媒体：选题与文案草稿',
            '灵感库与随笔记历史',
            '知识收集记录与链接库',
            '运行档案：全部操作流水',
            '上传媒体原件：media 下的图片 / 文档 / 视频',
            '数据库导出：知识相关数据表导出为可读 JSON',
        ],
        'excludes': [
            '平台程序代码与依赖环境（要连程序一起搬请用「迁移备份」）',
            '系统配置（含 AI 服务 API Key，不随知识资料外发）',
            '平台运行数据库原文件（知识记录已导出为 JSON，避免带入无关账号数据）',
        ],
        'restore_hint': '解包后运行包内「恢复知识.cmd」，把知识数据覆盖回平台对应目录；'
                        'IP 地址、端口、依赖环境均不受影响。',
        'srcs': [
            {'path': DATA_ROOT, 'dest': 'ZhiShi', 'filter': 'data'},
            {'path': CODE_ROOT / 'media', 'dest': 'media', 'filter': 'data'},
        ],
        'db_dump': KNOWLEDGE_MODELS,
        'embed_db': False,
        'with_installer': False,
        'with_skeleton': False,
    },

    'system': {
        'kind': 'system',
        'title': '系统备份',
        'subtitle': '生成不含知识数据的空运行平台',
        'icon': 'bi-box-seam',
        'tone': 'primary',
        'badge': '只含程序 · 零知识数据',
        'purpose': '打包平台本身的程序代码、模板、静态资源、依赖清单与外部工具，'
                   '并附带一键安装脚本，产出一个「空运行平台」。'
                   '发给他人即可安装出同款平台，你的任何知识资料都不会被带走。',
        'includes': [
            '全部后端源码：11 个功能模块 + core 核心层 + 配置与路由',
            '全部前端页面模板与静态资源（CSS / JS）',
            '依赖清单 requirements.txt（安装脚本据此自动装齐依赖）',
            '外部工具 ffmpeg.exe（视频转图文所需）',
            '一键安装脚本（检测 Python → 建虚拟环境 → 装依赖 → 建空库 → 初始化）',
            '空知识库骨架目录（02～05 及知识库 11 个子目录）',
            '安装说明（中文）',
        ],
        'excludes': [
            '任何知识资料：知识库 / 书架 / 办公平台 / 自媒体 / 灵感库 / 链接库',
            '运行数据库 db.sqlite3（安装脚本会新建一个空库）',
            '上传媒体原件（media 内容清空，仅保留空目录）',
            '虚拟环境 venv（含本机绝对路径，跨机不可用；由安装脚本按依赖清单重建）',
            '运行日志、缓存 __pycache__、压缩包与临时文件',
            '系统配置与 AI 服务 API Key',
        ],
        'restore_hint': '把整包发给他人，对方解压后双击「一键安装.cmd」即可装好并启动，'
                        '全程只需本机装有 Python 3.10+。',
        'srcs': [
            {'path': CODE_ROOT, 'dest': 'XiTong', 'filter': 'code'},
        ],
        'db_dump': [],
        'embed_db': False,
        'with_installer': True,
        'with_skeleton': True,
        'options': [
            {'key': 'include_ffmpeg', 'label': '包含外部工具 ffmpeg.exe',
             'hint': '视频转图文功能需要；约 157 MB，取消勾选可显著减小包体积。',
             'default': True, 'hint_bytes': 157 * 1024 * 1024},
        ],
    },

    'migration': {
        'kind': 'migration',
        'title': '迁移备份',
        'subtitle': '系统 + 知识全量，用于换电脑',
        'icon': 'bi-truck',
        'tone': 'success',
        'badge': '程序 + 知识 全量',
        'purpose': '把「系统备份」与「知识备份」合为一个整包：既有可一键安装的平台程序，'
                   '也含全部知识资料与数据库原文件。换机时在新电脑解压、一键安装，'
                   '平台运行与知识内容一并恢复。',
        'includes': [
            '系统备份的全部内容：源码 / 模板 / 静态资源 / 依赖清单 / 外部工具',
            '知识备份的全部内容：知识库 / WiKI / 书架 / 办公平台 / 自媒体 / 灵感库等磁盘正文',
            '运行数据库 db.sqlite3 原文件（全部记录的权威载体，恢复后零丢失）',
            '数据库知识数据 JSON 导出（便于核对与应急回填）',
            '上传媒体原件（media 原样保留）',
            '系统配置（平台名称 / 主题 / 个人表达风格 / AI 服务配置）',
            '一键安装脚本与中文安装说明',
        ],
        'excludes': [
            '虚拟环境 venv（含本机绝对路径，新机由安装脚本重建）',
            '运行日志、缓存 __pycache__、临时文件',
            '历史备份产物本身（备份输出目录不回卷）',
        ],
        'restore_hint': '新电脑解压后双击「一键安装.cmd」，脚本自动建环境并沿用包内数据库，'
                        '安装完成即恢复原平台的运行状态与全部知识资料。',
        'srcs': [
            {'path': CODE_ROOT, 'dest': 'XiTong', 'filter': 'code_with_data'},
            {'path': DATA_ROOT, 'dest': 'ZhiShi', 'filter': 'data'},
        ],
        'db_dump': KNOWLEDGE_MODELS,
        'embed_db': True,
        'with_installer': True,
        'with_skeleton': True,
        'options': [
            {'key': 'include_ffmpeg', 'label': '包含外部工具 ffmpeg.exe',
             'hint': '视频转图文功能需要；约 157 MB，取消勾选可显著减小包体积。',
             'default': True, 'hint_bytes': 157 * 1024 * 1024},
        ],
    },
}


def default_options(kind):
    """某类备份的可选项默认值。"""
    return {o['key']: o.get('default', True) for o in scope_spec(kind).get('options', [])}


def merge_options(kind, options=None):
    """把用户传入的可选项与默认值合并（缺省项用默认值补齐）。"""
    merged = default_options(kind)
    for k, v in (options or {}).items():
        if k in merged:
            merged[k] = bool(v)
    return merged


def scope_spec(kind):
    """取某个备份类型（knowledge / system / migration）的范围定义。"""
    spec = SCOPES.get(kind)
    if not spec:
        raise KeyError('未知的备份类型：%s' % kind)
    return spec


def all_specs():
    """按固定顺序返回三类范围定义，供前端渲染。"""
    return [SCOPES[k] for k in ('knowledge', 'system', 'migration')]
