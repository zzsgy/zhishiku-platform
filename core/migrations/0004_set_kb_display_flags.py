from django.db import migrations


def set_kb_display_flags(apps, schema_editor):
    """运行档案库不纳入知识库展示；知识库纳入。"""
    KnowledgeBase = apps.get_model('core', 'KnowledgeBase')
    KnowledgeBase.objects.filter(kind='runtime').update(show_in_kb=False)
    KnowledgeBase.objects.filter(kind='knowledge').update(show_in_kb=True)


def reverse_flags(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_knowledgebase_show_in_kb_linkitem'),
    ]

    operations = [
        migrations.RunPython(set_kb_display_flags, reverse_flags),
    ]
