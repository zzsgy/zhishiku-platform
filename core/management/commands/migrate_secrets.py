from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import SystemConfig
from core import ai
from core.secrets import store_secret


class Command(BaseCommand):
    help = '将旧版明文令牌移入本机系统凭据存储，全部成功后更新数据库引用'
    @transaction.atomic
    def handle(self, *args, **options):
        ai.migrate_legacy_key()
        providers = ai.get_providers()
        for provider in providers:
            key = provider.get('api_key') or ''
            if key:
                provider['api_key'] = store_secret('ai-' + provider['id'], key)
        token = SystemConfig.get_value('gitee_token')
        reference = store_secret('gitee', token) if token else ''
        ai._save_providers(providers)
        SystemConfig.set_value('gitee_token', reference)
        self.stdout.write('凭据迁移完成；换电脑时需要重新配置，备份不携带本机凭据')
