from django.db import models


class Book(models.Model):
    STATUS = [('unread', '未读'), ('reading', '在读'), ('done', '读完')]
    title = models.CharField('书名', max_length=256)
    author = models.CharField('作者', max_length=128, blank=True)
    category = models.CharField('分类', max_length=64, blank=True)
    file_path = models.CharField('文件/路径', max_length=512, blank=True)
    source_url = models.CharField('来源链接', max_length=512, blank=True, default='')
    cover = models.CharField('封面', max_length=512, blank=True)
    total_pages = models.IntegerField('总页数', default=0)
    progress = models.IntegerField('当前进度(页)', default=0)
    status = models.CharField('状态', max_length=16, choices=STATUS, default='unread')
    created = models.DateTimeField('添加时间', auto_now_add=True)

    class Meta:
        verbose_name = '书籍'
        verbose_name_plural = '书籍'
        ordering = ['-created']

    def __str__(self):
        return self.title


class ReadingNote(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, verbose_name='书籍', related_name='notes')
    location = models.CharField('位置', max_length=64, blank=True)
    note = models.TextField('笔记', blank=True)
    created = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '读书笔记'
        verbose_name_plural = '读书笔记'
        ordering = ['-created']

    def __str__(self):
        return f'{self.book} 笔记'


class GoldenSentence(models.Model):
    text = models.TextField('金句')
    source = models.CharField('出处', max_length=256, blank=True)
    # 显式归属：划词存金句时绑定到具体书籍/知识节点，保证「本书金句 / 本文金句」持久且唯一归属
    book = models.ForeignKey(
        'bookshelf.Book', on_delete=models.CASCADE, null=True, blank=True,
        related_name='goldens', verbose_name='所属书籍')
    node = models.ForeignKey(
        'core.KnowledgeNode', on_delete=models.CASCADE, null=True, blank=True,
        related_name='goldens', verbose_name='所属节点')
    created = models.DateTimeField('收录时间', auto_now_add=True)

    class Meta:
        verbose_name = '金句'
        verbose_name_plural = '金句'
        ordering = ['-created']

    def __str__(self):
        return self.text[:30]
