"""系统设置相关模型。

目前只有一项：BackupJob —— 三类备份（知识备份 / 系统备份 / 迁移备份）的执行记录。

为什么落库而不是只放内存：
  * 打包一个大包要几分钟，前端靠轮询看进度，进度必须能被跨请求读到；
  * 历史备份列表要能在刷新页面、重启服务后依然存在；
  * 一键安装包的信息（路径、体积、文件数、范围快照）要可追溯、可核对。
"""
import os

from django.db import models


class BackupJob(models.Model):
    KINDS = [
        ('knowledge', '知识备份'),
        ('system', '系统备份'),
        ('migration', '迁移备份'),
    ]
    STATUS = [
        ('pending', '排队中'),
        ('running', '进行中'),
        ('done', '已完成'),
        ('failed', '失败'),
    ]

    kind = models.CharField('备份类型', max_length=16, choices=KINDS)
    status = models.CharField('状态', max_length=16, choices=STATUS, default='pending')
    progress = models.IntegerField('进度(%)', default=0)
    stage = models.CharField('当前阶段', max_length=80, blank=True)
    message = models.TextField('结果说明', blank=True)

    package_path = models.TextField('备份包路径', blank=True)
    package_name = models.CharField('包名', max_length=200, blank=True)
    package_size = models.BigIntegerField('包体积(字节)', default=0)
    file_count = models.IntegerField('文件数', default=0)

    # 本次勾选的可选项（JSON）与范围清单快照（JSON）——事后核对「这个包到底是什么范围」
    options_json = models.TextField('选项', blank=True)
    scope_json = models.TextField('范围清单快照', blank=True)

    created = models.DateTimeField('开始时间', auto_now_add=True)
    finished = models.DateTimeField('结束时间', null=True, blank=True)

    class Meta:
        verbose_name = '备份任务'
        verbose_name_plural = '备份任务'
        ordering = ['-created']

    def __str__(self):
        return f'{self.get_kind_display()} · {self.created:%Y-%m-%d %H:%M}'

    # -- 供模板/接口使用的小工具属性 -------------------------------------
    @property
    def package_filename(self):
        return os.path.basename(self.package_path) if self.package_path else ''

    @property
    def package_exists(self):
        """包是否还在磁盘上（用户可能已手工挪走或删除）。"""
        try:
            return bool(self.package_path) and os.path.isfile(self.package_path)
        except OSError:
            return False

    @property
    def is_active(self):
        return self.status in ('pending', 'running')

    def to_dict(self):
        import json
        return {
            'id': self.pk,
            'kind': self.kind,
            'kind_label': self.get_kind_display(),
            'status': self.status,
            'status_label': self.get_status_display(),
            'progress': self.progress,
            'stage': self.stage,
            'message': self.message,
            'package_name': self.package_name,
            'package_path': self.package_path,
            'package_filename': self.package_filename,
            'package_size': self.package_size,
            'package_exists': self.package_exists,
            'file_count': self.file_count,
            'created': self.created.strftime('%Y-%m-%d %H:%M:%S') if self.created else '',
            'finished': self.finished.strftime('%Y-%m-%d %H:%M:%S') if self.finished else '',
            'options': json.loads(self.options_json) if self.options_json else {},
            'active': self.is_active,
        }
