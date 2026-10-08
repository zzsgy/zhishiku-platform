from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyMigrationTests(TransactionTestCase):
    def test_existing_relations_and_lost_archive_categories_are_preserved(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old = [('core','0012_alter_linkitem_unparse_reason'), ('office','0003_advise_report_file_path')]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            Base=apps.get_model('core','KnowledgeBase')
            Node=apps.get_model('core','KnowledgeNode')
            Edge=apps.get_model('core','Edge')
            Record=apps.get_model('office','WorkRecord')
            base=Base.objects.create(name='原有库',directory='knowledge')
            left=Node.objects.create(base=base,title='原有知识')
            right=Node.objects.create(base=base,title='目标')
            Edge.objects.create(source=left,target=right,kind='link',label='第一条')
            Edge.objects.create(source=left,target=right,kind='link',label='第二条')
            record=Record.objects.create(category='archive',content='历史分类无法推断')
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from core.models import DeletedNode, Edge as NewEdge, KnowledgeNode
            from office.models import WorkRecord
            self.assertEqual(NewEdge.objects.filter(source_id=left.pk,target_id=right.pk,kind='link').count(),1)
            archive=DeletedNode.objects.get(original_id__lt=0)
            self.assertEqual([row['label'] for row in archive.snapshot['records']],['第一条','第二条'])
            self.assertEqual(KnowledgeNode.objects.get(pk=left.pk).title,'原有知识')
            migrated=WorkRecord.objects.get(pk=record.pk)
            self.assertTrue(migrated.is_archived)
            self.assertEqual(migrated.category,'archive')
        finally:
            MigrationExecutor(connection).migrate(latest)
