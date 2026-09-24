from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bookshelf', '0003_alter_book_source_url'),
        ('core', '0006_alter_collectionitem_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='goldensentence',
            name='book',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='goldens', to='bookshelf.book', verbose_name='所属书籍'),
        ),
        migrations.AddField(
            model_name='goldensentence',
            name='node',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='goldens', to='core.knowledgenode', verbose_name='所属节点'),
        ),
    ]
