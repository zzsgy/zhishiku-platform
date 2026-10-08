from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '仅在缺少管理员时交互创建账号；不预置密码'

    def handle(self, *args, **options):
        if get_user_model().objects.filter(is_staff=True, is_active=True).exists():
            self.stdout.write('已有管理员账号，未修改任何密码')
            return
        call_command('createsuperuser', interactive=True)
