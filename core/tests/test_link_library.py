from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from core.models import LinkItem


class LinkLibraryPages(TestCase):
    def test_empty_and_populated_library_use_collection_time(self):
        self.client.force_login(get_user_model().objects.create_user('link-reader', is_staff=True))
        self.assertEqual(self.client.get('/knowledgebase/links/').status_code, 200)
        first = LinkItem.objects.create(url='https://example.com/older', title='较早收集')
        second = LinkItem.objects.create(url='https://example.com/latest', title='最新收集')
        LinkItem.objects.filter(pk=first.pk).update(collected_at=timezone.now() - timedelta(days=3))
        response = self.client.get('/knowledgebase/links/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context['items']], [second.pk, first.pk])
        self.assertContains(response, '最新收集')
        self.assertContains(response, '较早收集')
