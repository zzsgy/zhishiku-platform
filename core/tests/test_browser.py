import os
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings, tag
from core.models import KnowledgeBase, KnowledgeNode
from office.models import WorkRecord


@tag('browser')
class BrowserFlows(StaticLiveServerTestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        root = Path(self.workspace.name)
        self.override = override_settings(MEDIA_ROOT=root/'media', ZHI_SHI_ROOT=root/'data',
                                          BACKUP_ROOT=root/'backup', ALLOWED_HOSTS=['localhost','127.0.0.1','testserver'])
        self.override.enable()
        self.addCleanup(self.override.disable)
        get_user_model().objects.create_user('browser-tester', password='browser-tests-only-123', is_staff=True)
        self.base = KnowledgeBase.objects.create(name='浏览器测试库', directory='knowledge')
        WorkRecord.objects.create(category='ops', content='浏览器测试工作', status='进行中')

    def test_office_refresh_import_draft_and_mobile(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest('请安装 requirements-test.txt 和 Chromium 后执行浏览器测试')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width':1440,'height':960})
            errors = []
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.goto(self.live_server_url + '/office/')
            page.get_by_label('用户名').fill('browser-tester')
            page.get_by_label('密码').fill('browser-tests-only-123')
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url('**/office/')
            page.locator('[data-office-tab="files"]').click()
            page.locator('a[data-filter-link][href*="cat=ops"]').first.click()
            page.wait_for_function("!document.getElementById('office-scope').classList.contains('is-loading')")
            page.locator('[data-office-tab="reports"]').click()
            expect(page.locator('[data-office-panel="reports"]')).to_be_visible()
            page.go_back()
            page.wait_for_function("!document.getElementById('office-scope').classList.contains('is-loading')")
            page.go_forward()
            page.wait_for_function("!document.getElementById('office-scope').classList.contains('is-loading')")
            expect(page.locator('[data-office-panel="reports"]')).to_be_visible()
            page.locator('[data-office-tab="reports"]').focus()
            page.keyboard.press('ArrowRight')
            expect(page.locator('[data-office-tab="files"]')).to_have_attribute('aria-selected','true')
            page.locator('[data-office-tab="records"]').click()
            with page.expect_response('**/office/api/record_update/') as update:
                page.locator('.wr-check').first.check()
            self.assertEqual(update.value.status, 200)
            page.goto(self.live_server_url + '/collection/')
            content = '# 浏览器导入\n\n中文正文。\n\n| 项目 | 数值 |\n| --- | --- |\n| 温度 | 42 |\n\n```python\nprint("中文")\n```\n\n<script>window.injected=true</script>\n'
            page.locator('#localImportFile').set_input_files({'name':'browser.md', 'mimeType':'text/markdown', 'buffer':content.encode()})
            with page.expect_response('**/collection/import_local/') as imported:
                page.locator('#localImportForm button').first.click()
            self.assertTrue(imported.value.json()['ok'])
            from concurrent.futures import ThreadPoolExecutor
            def node_id():
                from django.db import connections
                try:
                    return KnowledgeNode.objects.get(title='browser').pk
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as executor:
                from types import SimpleNamespace
                node = SimpleNamespace(pk=executor.submit(node_id).result())
            page.goto(self.live_server_url + f'/wiki/node/{node.pk}/')
            expect(page.locator('.kb-prose table')).to_be_visible()
            expect(page.locator('.kb-prose pre code')).to_contain_text('print("中文")')
            self.assertIsNone(page.evaluate('window.injected'))
            page.goto(self.live_server_url + f'/wiki/node/{node.pk}/edit/')
            draft_key = 'kbmd:' + page.locator('#mdRich').get_attribute('data-draft-namespace') + ':' + str(node.pk)
            page.locator('#mdModeBtn').click()
            revised = content.replace('<script>window.injected=true</script>', '新修订需要保存。')
            page.locator('#mdInput').fill(revised)
            edit_url = self.live_server_url + f'/wiki/node/{node.pk}/edit/'
            def reject_save(route):
                if route.request.method == 'POST':
                    route.fulfill(status=500,body='故障注入：保存失败')
                else:
                    route.continue_()
            page.route(edit_url, reject_save)
            page.locator('#mdForm button[type="submit"]').click()
            page.wait_for_load_state('load')
            self.assertIsNotNone(page.evaluate('key => localStorage.getItem(key)', draft_key))
            page.unroute(edit_url, reject_save)
            page.goto(edit_url)
            expect(page.locator('#mdDraftUse')).to_be_visible()
            page.locator('#mdDraftUse').click()
            page.wait_for_function("document.getElementById('mdRich').textContent.includes('新修订需要保存')")
            page.locator('#mdForm button[type="submit"]').click()
            page.wait_for_url('**/?saved=1')
            self.assertIsNone(page.evaluate('key => localStorage.getItem(key)', draft_key))
            expect(page.locator('.kb-prose table')).to_be_visible()
            expect(page.locator('.kb-prose pre code')).to_contain_text('print("中文")')
            page.set_viewport_size({'width':390,'height':844})
            page.goto(self.live_server_url + '/collection/')
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
            self.assertEqual(page.locator('#collectionForms > div:visible').count(), 1)
            page.locator('#kbMenuToggle').click()
            expect(page.locator('#kbMenuToggle')).to_have_attribute('aria-expanded','true')
            page.keyboard.press('Escape')
            expect(page.locator('#kbMenuToggle')).to_have_attribute('aria-expanded','false')
            page.locator('[data-import-mode="2"]').click()
            self.assertEqual(page.locator('#collectionForms > div:visible').count(), 1)
            artifacts = os.environ.get('ZHISHIKU_TEST_ARTIFACTS')
            if artifacts:
                destination=Path(artifacts)
                destination.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(destination/'收集页_390px.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':960})
                page.goto(self.live_server_url + '/office/#reports')
                page.screenshot(path=str(destination/'办公报告页_1440px.png'),full_page=True)
            page.goto(self.live_server_url + '/settings/#ai')
            page.locator('#aiAddBtn').click()
            expect(page.locator('#aiModal')).to_be_visible()
            page.locator('#aiF_name').fill('浏览器本机服务')
            page.locator('#aiF_model').fill('test-model')
            page.locator('#aiF_base').fill('http://127.0.0.1:11434/v1')
            page.locator('#aiF_local').check()
            page.locator('#aiF_auth').uncheck()
            with page.expect_response('**/settings/ai/save/') as configuration:
                page.locator('#aiF_save').click()
            self.assertEqual(configuration.value.status, 200)
            expect(page.locator('#aiProvTable').get_by_text('浏览器本机服务', exact=True)).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()
