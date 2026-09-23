from django.db import models


class Inspiration(models.Model):
    text = models.TextField('内容')
    source_ref = models.CharField('来源', max_length=256, blank=True, default='', help_text='如 某书/某文/划句来源')
    context = models.CharField('上下文', max_length=512, blank=True, default='', help_text='记录时的背景或补充上下文，可选')
    created = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '灵感'
        verbose_name_plural = '灵感'
        ordering = ['-created']

    def __str__(self):
        return self.text[:30]


class NoteLog(models.Model):
    """随笔记记入历史：每次从随笔记页成功写入某库，落一条日志。"""

    TARGET_CHOICES = [
        ('insp', '灵感库'),
        ('gold', '金句库'),
        ('kb', '知识库'),
    ]
    target = models.CharField('记入库', max_length=8, choices=TARGET_CHOICES)
    text = models.TextField('内容')
    created = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '随笔记历史'
        verbose_name_plural = '随笔记历史'
        ordering = ['-created']

    def __str__(self):
        return f'{self.get_target_display()}：{self.text[:20]}'
