from django.core.management.base import BaseCommand, CommandError
from systemsettings.reliable_backup import verify


class Command(BaseCommand):
    help = '校验备份清单、路径与逐文件哈希，不覆盖任何数据'
    def add_arguments(self, parser):
        parser.add_argument('package')
    def handle(self, *args, **options):
        try:
            manifest = verify(options['package'])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"校验通过：{len(manifest['files'])} 个文件"))
