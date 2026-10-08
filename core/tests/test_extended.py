import io
import json
from pathlib import Path
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from core.models import DeletedNode, Edge, KnowledgeBase, KnowledgeNode, NodeNote, SystemConfig
from core.services import recycle_node, restore_node, sync_node_to_file
from core.tests.test_regressions import IsolatedCase
from bookshelf.models import GoldenSentence


class ExtendedTests(IsolatedCase):
    def test_invalid_office_and_image_uploads_leave_no_asset(self):
        from core.storage import save_upload
        from core.models import Asset
        for filename in ('bad.docx', 'bad.png'):
            with self.assertRaises(ValueError):
                save_upload(SimpleUploadedFile(filename, b'invalid document bytes'))
        self.assertEqual(Asset.objects.count(), 0)
        self.assertFalse(any(self.settings_override.options['MEDIA_ROOT'].rglob('.upload-*')))

    def test_create_failure_rolls_back_database_and_never_exports(self):
        with patch('wiki.views.auto_link_edges', side_effect=RuntimeError('relation failure')), \
             patch('wiki.views.sync_node_to_file') as publish:
            with self.assertRaises(RuntimeError):
                self.client.post('/wiki/create/', {'base': self.base.pk, 'title': '失败创建', 'content_md': '正文'})
        self.assertFalse(KnowledgeNode.objects.filter(title='失败创建').exists())
        publish.assert_not_called()

    def test_pdf_pages_and_code_are_safe_and_traceable(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from core.document_parser import parse
        from core.services import render_markdown
        writer=PdfWriter()
        page=writer.add_blank_page(width=300,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 30 250 Td (print 42) Tj 0 -20 Td (<script>unsafe</script>) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO()
        writer.write(output)
        text,metadata=parse(output.getvalue(),'code.pdf')
        self.assertEqual(metadata['blocks'][0]['page'],1)
        self.assertIn('print 42',text)
        html=render_markdown(text)
        self.assertIn('kb-plain-text',html)
        self.assertNotIn('<script>',html)
        self.assertTrue(metadata['warnings'])

    def test_frame_count_and_process_time_are_bounded(self):
        from core.media import extract_keyframes
        with patch('core.media._ensure_ffmpeg'), patch('core.media.subprocess.run') as process:
            extract_keyframes('test.mp4', Path(self.workspace.name)/'frames', max_frames=999)
        command=process.call_args.args[0]
        self.assertEqual(command[command.index('-frames:v')+1],'60')
        self.assertEqual(process.call_args.kwargs['timeout'],120)

    def test_cloud_ai_requires_explicit_outbound_permission(self):
        from core.ai_result import call
        provider={'id':'cloud','api_key':'fake','base_url':'https://api.example.invalid/v1','model':'test'}
        with patch.dict('os.environ', {'ZHISHIKU_ALLOW_AI_OUTBOUND':'0'}), patch('requests.post') as transport:
            result=call(provider,'system','private text')
        self.assertEqual(result.error_code,'outbound_disabled')
        transport.assert_not_called()

    def test_forged_success_url_never_clears_draft_without_receipt(self):
        node=self.node()
        response=self.client.get(f'/wiki/node/{node.pk}/?saved=1')
        self.assertNotContains(response,'confirmed-md')

    def test_recycle_and_restore_preserves_related_records(self):
        node = self.node()
        target = KnowledgeNode.objects.create(base=self.base, title='目标')
        child = KnowledgeNode.objects.create(base=self.base, title='孩子', parent=node)
        NodeNote.objects.create(node=node, note='需要保留的笔记')
        GoldenSentence.objects.create(node=node, text='需要保留的金句')
        Edge.objects.create(source=node, target=target)
        sync_node_to_file(node)
        node.content_md = '修订内容'
        node.save()
        pk = node.pk
        recycle_node(node)
        self.assertFalse(KnowledgeNode.objects.filter(pk=pk).exists())
        restored = restore_node(DeletedNode.objects.get(original_id=pk))
        self.assertEqual(restored.content_md, '修订内容')
        self.assertEqual(restored.notes.get().note, '需要保留的笔记')
        self.assertEqual(restored.goldens.count(), 1)
        self.assertEqual(restored.revisions.count(), 1)
        self.assertEqual(restored.edges_out.count(), 1)
        child.refresh_from_db()
        self.assertEqual(child.parent_id, pk)
        self.assertEqual(restored.export_status, 'done')

    def test_parent_must_share_library_and_be_acyclic(self):
        node = self.node()
        child = KnowledgeNode.objects.create(base=self.base, title='孩子', parent=node)
        node.parent = child
        with self.assertRaises(ValidationError):
            node.save()
        node.parent = None
        node.base = KnowledgeBase.objects.create(name='另一库')
        node.parent = child
        with self.assertRaises(ValidationError):
            node.save()

    def test_provider_id_is_string_and_key_is_never_returned(self):
        self.client.force_login(self.admin)
        with patch('core.secrets.store_secret', return_value='env:TEST_AI_KEY'):
            response = self.client.post('/settings/ai/save/', {'id': 'my-provider', 'name': '本机',
                'type': 'openai', 'model': 'test-model', 'api_key': 'fake-key',
                'base_url': 'http://127.0.0.1:11434/v1', 'enabled': True,
                'local': True, 'auth_required': False}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('fake-key', SystemConfig.get_value('ai_providers'))
        self.assertNotContains(self.client.get('/settings/'), 'fake-key')
        with patch('requests.post', return_value=Mock(status_code=200, json=lambda:{'choices':[{'message':{'content':'ok'}}]})) as upstream:
            result = self.client.post('/settings/ai/test/my-provider/')
        self.assertTrue(result.json()['ok'])
        self.assertNotIn('Authorization', upstream.call_args.kwargs['headers'])

    def test_bad_get_id_and_stale_selfmedia_edit_are_rejected(self):
        self.assertEqual(self.client.get('/knowledgebase/?base=not-a-number').status_code, 400)
        node = self.node(category='自媒体')
        url = f'/selfmedia/edit/{node.pk}/'
        self.assertEqual(self.client.post(url, {'title':'覆盖', 'content_md':'正文'}).status_code, 409)

    def test_office_and_library_reference_same_asset(self):
        content = '同一个文件的内容'.encode()
        for url, fields in [('/collection/import_office/', {'office_cat':'ops'}), ('/collection/import_local/', {})]:
            response = self.client.post(url, {**fields, 'file':SimpleUploadedFile('same.txt',content)},
                                        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertTrue(response.json()['ok'])
        from core.models import Asset, WorkFileIndex
        self.assertEqual(Asset.objects.count(), 1)
        entry = WorkFileIndex.objects.get()
        self.assertTrue(entry.original_path.startswith('media:assets/'))
        response = self.client.get(f'/office/workfile_open/{entry.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        response.close()

    def test_empty_pdf_retains_original_and_reports_failure(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        buffer = io.BytesIO()
        writer.write(buffer)
        response = self.client.post('/collection/import_local/', {'file':SimpleUploadedFile('scan.pdf',buffer.getvalue())},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertFalse(response.json()['ok'])
        from core.models import Asset, ParsedDocument
        self.assertTrue((Path(self.settings_override.options['MEDIA_ROOT']) / Asset.objects.get().storage_path).exists())
        self.assertEqual(ParsedDocument.objects.count(), 0)

    def test_graph_is_bounded_and_exports_are_streaming(self):
        KnowledgeNode.objects.bulk_create([KnowledgeNode(base=self.base, title=f'节点{i}') for i in range(320)])
        graph = self.client.get('/starmap/')
        self.assertEqual(len(graph.context['graph_data']['nodes']), 300)
        self.assertTrue(graph.context['limited'])
        for kind in ('csv', 'md'):
            response = self.client.get(f'/runarchive/export/{kind}/')
            self.assertTrue(response.streaming)
            b''.join(response.streaming_content)

    def test_diagnostic_retry_updates_explicit_export_state(self):
        node = self.node()
        with patch('core.services.atomic_write', side_effect=OSError('disk unavailable')):
            sync_node_to_file(node)
        self.client.force_login(self.admin)
        self.client.post('/settings/diagnostics/', {'node':node.pk})
        node.refresh_from_db()
        self.assertEqual(node.export_status, 'done')
