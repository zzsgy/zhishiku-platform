from django.db import models
import os


class WorkRecord(models.Model):
    # 前三个是工作分类；archive 为「归档分类」，用于放置已归档的工作记录
    # （由日期卡片上的「归档」按钮写入，默认列表中不再展示）。
    ARCHIVE_KEY = 'archive'
    CATEGORY = [('admin', '行政管理'), ('party', '基层党建'), ('ops', '网络运维'),
                (ARCHIVE_KEY, '工作记录')]
    category = models.CharField('类别', max_length=16, choices=CATEGORY, default='admin')
    content = models.TextField('内容')
    status = models.CharField('状态', max_length=32, blank=True)
    date = models.DateField('日期', auto_now_add=True)

    class Meta:
        verbose_name = '工作记录'
        verbose_name_plural = '工作记录'
        ordering = ['-date']

    def __str__(self):
        return f'[{self.get_category_display()}] {self.content[:20]}'


class Advise(models.Model):
    """AI 献策：每一次「生成献策」都会留下一条记录 + 一份 Markdown 文件。

    落盘规则见 office/storage.py（唯一实现）：
      C:\\ZSK\\ZhiShi\\03_知识库\\10_办公平台\\献策输出\\<工作分类>\\<YYYY.MM.DD>_<主题>.md

    file_path 只是「留痕」，即使落盘失败（权限/占用）也照常保留数据库记录——
    前端列表与统一搜索都以本表为权威索引，磁盘文件用于人工翻阅与 Obsidian 归档。
    """
    SOURCE = [('ai', 'AI 生成'), ('local', '本地汇总')]
    # 复用 WorkRecord 的工作分类（行政管理 / 基层党建 / 网络运维 / 工作记录），保持口径一致
    category = models.CharField('工作类别', max_length=16, choices=WorkRecord.CATEGORY, default='admin')
    topic = models.CharField('主题', max_length=200)
    content_md = models.TextField('献策内容(Markdown)', blank=True)
    source = models.CharField('生成方式', max_length=16, choices=SOURCE, default='ai')
    file_path = models.TextField('输出文件路径', blank=True)
    created = models.DateTimeField('生成时间', auto_now_add=True)

    class Meta:
        verbose_name = 'AI 献策'
        verbose_name_plural = 'AI 献策'
        ordering = ['-created']

    @property
    def date_stamp(self):
        return self.created.strftime('%Y.%m.%d') if self.created else ''

    @property
    def title(self):
        """献策名称：`YYYY.MM.DD 主题`（列表 / 详情页 / 输出文件名共用同一口径）。"""
        return ('%s %s' % (self.date_stamp, self.topic)).strip()

    @property
    def file_name(self):
        """输出文件名（不含目录）；未落盘时为空。"""
        return os.path.basename(self.file_path) if self.file_path else ''

    def __str__(self):
        return self.title


class Report(models.Model):
    KIND = [('daily', '日报'), ('weekly', '周报'), ('monthly', '月报'), ('yearly', '年报')]
    kind = models.CharField('类型', max_length=16, choices=KIND)
    content_md = models.TextField('内容(Markdown)', blank=True)
    range_start = models.DateField('起始', null=True, blank=True)
    range_end = models.DateField('结束', null=True, blank=True)
    file_path = models.TextField('输出文件路径', blank=True)
    created = models.DateTimeField('生成时间', auto_now_add=True)

    class Meta:
        verbose_name = '报告'
        verbose_name_plural = '报告'

    @property
    def range_label(self):
        """区间文案：单日只显示一天，跨日显示「起 ~ 止」。"""
        if not (self.range_start and self.range_end):
            return ''
        if self.range_start == self.range_end:
            return self.range_start.strftime('%Y.%m.%d')
        return '%s ~ %s' % (self.range_start.strftime('%Y.%m.%d'),
                            self.range_end.strftime('%Y.%m.%d'))

    @property
    def title(self):
        """报告名称（唯一命名规则的实现，列表 / 详情页 / 页面标题共用）：

        - 日报：`2026.09.21日报`（单日口径，取区间结束日）
        - 其余：`2026.09.01~2026.09.30月报`（起始~结束 + 报告标签）
        - 缺区间的历史数据退化为「标签 + 生成日期」，保证不出现空标题。
        """
        label = self.get_kind_display()
        created = self.created.strftime('%Y.%m.%d') if self.created else ''
        if not self.range_end:
            return ('%s %s' % (label, created)).strip()
        if self.kind == 'daily' or not self.range_start:
            return self.range_end.strftime('%Y.%m.%d') + label
        return '%s~%s%s' % (self.range_start.strftime('%Y.%m.%d'),
                            self.range_end.strftime('%Y.%m.%d'), label)

    @property
    def file_name(self):
        """输出文件名（不含目录）；未落盘时为空。"""
        return os.path.basename(self.file_path) if self.file_path else ''

    def __str__(self):
        return self.title
