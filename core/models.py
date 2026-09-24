"""Shared core models for the knowledge platform.

These models back multiple modules: knowledge bases, knowledge nodes (wiki /
knowledge base entries), relation edges (star map + wiki backlinks), collection
items, operation logs and system configuration.
"""
from django.db import models


class KnowledgeBase(models.Model):
    """逻辑分库：运行档案库 / 知识库。"""
    KIND_CHOICES = [
        ('runtime', '运行档案库'),
        ('knowledge', '知识库'),
    ]
    name = models.CharField('名称', max_length=64)
    kind = models.CharField('类型', max_length=16, choices=KIND_CHOICES, default='knowledge')
    description = models.TextField('说明', blank=True)
    directory = models.CharField(
        '映射目录（相对 C:\\ZSK\\ZhiShi）',
        max_length=256,
        blank=True,
        help_text='如：03_知识库/08_WIKI，留空则使用默认目录'
    )
    created = models.DateTimeField('创建时间', auto_now_add=True)
    show_in_kb = models.BooleanField('在知识库展示', default=True)

    class Meta:
        verbose_name = '知识库'
        verbose_name_plural = '知识库'

    def __str__(self):
        return self.name


class KnowledgeNode(models.Model):
    """通用知识节点：WiKI 层与知识库共用。"""
    NODE_TYPE = [
        ('wiki', 'WiKI 页'),
        ('article', '文章'),
        ('doc', '资料'),
        ('note', '笔记'),
    ]
    base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, verbose_name='所属库', null=True, blank=True)
    title = models.CharField('标题', max_length=256)
    content_md = models.TextField('正文(Markdown)', blank=True)
    node_type = models.CharField('类型', max_length=16, choices=NODE_TYPE, default='wiki')
    category = models.CharField('分类', max_length=64, blank=True)
    tags = models.CharField('标签', max_length=256, blank=True, help_text='逗号分隔')
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='父节点')
    source = models.CharField('来源', max_length=256, blank=True)
    # 知识沉淀：来源追踪 + 草稿门控（与原始文档解耦，原始删除不影响本节点）
    # 两态：pending=AI 提炼草稿 / adopted=已采纳进入正式知识层（上传文件直接入库为 adopted）
    PRECIP_STATUS = [('pending', '待处理'), ('adopted', '已采纳')]
    status = models.CharField('沉淀状态', max_length=16, choices=PRECIP_STATUS, default='adopted',
                              help_text='pending=AI 提炼草稿；adopted=已采纳进入正式知识层')
    origin_type = models.CharField('来源类型', max_length=24, blank=True,
                                   help_text='kb_node / office_advise / office_report / office_workfile / multi')
    origin_ref = models.CharField('来源引用', max_length=1024, blank=True,
                                  help_text='原始文档标识：节点 pk 或 office 记录 pk；multi 时存 JSON 列表')
    origin_title = models.CharField('来源标题', max_length=256, blank=True)
    ai_generated = models.BooleanField('AI 生成', default=False)
    created = models.DateTimeField('创建时间', auto_now_add=True)
    updated = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '知识节点'
        verbose_name_plural = '知识节点'
        ordering = ['-updated']

    def __str__(self):
        return self.title


class NodeNote(models.Model):
    """知识节点阅读笔记：与书架读书笔记对应的节点侧记笔记能力。"""
    node = models.ForeignKey(KnowledgeNode, on_delete=models.CASCADE, verbose_name='知识节点', related_name='notes')
    location = models.CharField('位置', max_length=64, blank=True)
    note = models.TextField('笔记', blank=True)
    created = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '节点笔记'
        verbose_name_plural = '节点笔记'
        ordering = ['-created']

    def __str__(self):
        return f'{self.node} 笔记'


class Edge(models.Model):
    """关系边：知识星图与 WiKI 反向链接。"""
    KIND = [('link', '链接'), ('backlink', '反向链接'), ('related', '相关')]
    source = models.ForeignKey(KnowledgeNode, on_delete=models.CASCADE, related_name='edges_out', verbose_name='源')
    target = models.ForeignKey(KnowledgeNode, on_delete=models.CASCADE, related_name='edges_in', verbose_name='目标')
    label = models.CharField('关系', max_length=64, blank=True)
    kind = models.CharField('类型', max_length=16, choices=KIND, default='related')
    created = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '关系'
        verbose_name_plural = '关系'

    def __str__(self):
        return f'{self.source} → {self.target}'


class CollectionItem(models.Model):
    """知识收集记录。

    状态流转（知识收集页「最近收集」按此实时展示导入进度与结果）：
    pending（进行中，已受理）→ done（成功）/ failed（失败）；
    note 保存该次导入的结果说明（落库位置、失败原因等），供页面直接展示反馈。
    """
    KIND = [('local', '本地导入'), ('web', '网页解析'), ('video', '视频转图文'),
            ('text', '文本输入'), ('image', '图片上传'), ('office', '存入办公平台')]
    STATUS = [('pending', '进行中'), ('done', '已完成'), ('failed', '失败')]
    kind = models.CharField('类型', max_length=16, choices=KIND)
    title = models.CharField('标题', max_length=256, blank=True)
    raw_text = models.TextField('原始内容', blank=True)
    parsed_md = models.TextField('解析结果(Markdown)', blank=True)
    source_url = models.CharField('来源链接', max_length=1024, blank=True)
    status = models.CharField('状态', max_length=16, choices=STATUS, default='pending')
    note = models.TextField('结果说明', blank=True)
    base = models.ForeignKey(KnowledgeBase, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='归入库')
    created = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '收集项'
        verbose_name_plural = '收集项'
        ordering = ['-created']

    def __str__(self):
        return self.title or f'{self.kind}-{self.pk}'


class OperationLog(models.Model):
    """运行档案：工作台操作流水。"""
    user = models.CharField('操作者', max_length=64, default='系统')
    module = models.CharField('模块', max_length=32)
    action = models.CharField('动作', max_length=64)
    detail = models.TextField('明细', blank=True)
    created = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志'
        ordering = ['-created']

    def __str__(self):
        return f'[{self.created:%Y-%m-%d %H:%M}] {self.module}/{self.action}'


class SystemConfig(models.Model):
    """工作台设置键值对。"""
    key = models.CharField('键', max_length=64, unique=True)
    value = models.TextField('值', blank=True)

    class Meta:
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    def __str__(self):
        return self.key

    @classmethod
    def get_value(cls, key, default=''):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj else default

    @classmethod
    def set_value(cls, key, value):
        obj, _ = cls.objects.get_or_create(key=key)
        obj.value = value
        obj.save()


class WorkFileIndex(models.Model):
    """工作文件索引目录：办公平台按分类展示工作文件，只读引用，不改写原件。

    - 文件来源：知识收集页「本地导入 → 存入办公平台」，按所选工作分类（行政管理 /
      基层党建 / 网络运维）把文件副本存到 03_知识库/11_工作文件/<分类>/，并在此登记。
    - source='upload'：original_path 指向平台保存的文件副本。
    - source='folder'：历史遗留来源（工作文件夹同步），新版本已不再产生此类记录。
    查看时通过 workfile_open 以只读方式调出副本，不触碰任何原始文件。
    """

    title = models.CharField('标题', max_length=255)
    original_path = models.TextField('原始文件路径')
    category = models.CharField('分类', max_length=128, default='未分类',
                                help_text='来自工作文件夹的子目录，或「上传文件」')
    source = models.CharField('来源', max_length=16, default='folder',
                             choices=[('folder', '工作文件夹'), ('upload', '上传文件')])
    ext = models.CharField('扩展名', max_length=16, blank=True)
    size = models.BigIntegerField('大小(字节)', default=0)
    mtime = models.FloatField('修改时间', default=0)
    extracted = models.TextField('提取内容', blank=True,
                                 help_text='解析出的纯文本 / Markdown，用于检索与概要展示')
    created = models.DateTimeField('登记时间', auto_now_add=True)
    updated = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '工作文件索引'
        verbose_name_plural = '工作文件索引'
        ordering = ['category', 'title']

    def __str__(self):
        return self.title


class LinkItem(models.Model):
    """知识收集过程中解析失败/无法解析的链接归档与重试队列。"""
    SOURCE_CHOICES = [
        ('web', '网页解析'),
        ('selection', '划词收藏'),
        ('bookmark', '书签导入'),
        ('import', '批量导入'),
        ('manual', '手动添加'),
    ]
    TYPE_CHOICES = [
        ('webpage', '网页'),
        ('wechat', '公众号文章'),
        ('bilibili', 'B站视频'),
        ('video', '视频'),
        ('paper', '论文'),
        ('pdf', 'PDF'),
        ('forum', '论坛'),
        ('social', '社交动态'),
        ('unknown', '未知'),
    ]
    REASON_CHOICES = [
        ('paywall', '付费墙'),
        ('js_render', 'JS动态渲染'),
        ('antibot', '反爬阻断'),
        ('unsupported', '格式不支持'),
        ('timeout', '超时'),
        ('login_wall', '登录墙'),
        # 抓取侧对 404/410 会给出 'invalid'；此前 choices 里没有这一项，
        # get_unparse_reason_display 会退化成直接显示英文键名 "invalid"。
        ('invalid', '链接失效'),
        ('unknown', '未知'),
    ]
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('archived', '链接归档'),
        ('invalid', '已作废'),
    ]

    url = models.URLField('链接', max_length=1024, unique=True)
    title = models.CharField('标题', max_length=256, blank=True)
    source = models.CharField('来源', max_length=16, choices=SOURCE_CHOICES, default='manual')
    link_type = models.CharField('类型', max_length=16, choices=TYPE_CHOICES, default='unknown')
    unparse_reason = models.CharField('无法解析原因', max_length=16, choices=REASON_CHOICES, default='unknown')
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='pending')
    tags = models.CharField('标签', max_length=256, blank=True, help_text='逗号分隔，如：待读,高优')
    note = models.TextField('备注', blank=True)
    related_base = models.ForeignKey(
        KnowledgeBase, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='关联知识库')
    collector = models.CharField('采集者', max_length=64, default='系统')
    domain = models.CharField('域名', max_length=128, blank=True)
    collected_at = models.DateTimeField('采集时间', auto_now_add=True)
    last_checked = models.DateTimeField('最近重试', null=True, blank=True)
    retry_count = models.IntegerField('重试次数', default=0)

    class Meta:
        verbose_name = '链接'
        verbose_name_plural = '链接库'
        ordering = ['-collected_at']

    def __str__(self):
        return self.title or self.url

    def save(self, *args, **kwargs):
        if not self.domain and self.url:
            try:
                from urllib.parse import urlparse
                self.domain = urlparse(self.url).netloc
            except Exception:
                pass
        super().save(*args, **kwargs)
