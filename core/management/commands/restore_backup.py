from django.core.management.base import BaseCommand, CommandError
from systemsettings.restore import restore


class Command(BaseCommand):
    help = '校验 ZIP 并恢复到尚不存在的新目录，禁止覆盖现有平台'

    def add_arguments(self, parser):
        parser.add_argument('package')
        parser.add_argument('--destination', required=True)

    def handle(self, *args, **options):
        try:
            report = restore(options['package'], options['destination'])
        except (ValueError, RuntimeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write('恢复验证完成；需重新配置本机凭据，外部目录待核对：%d' % len(report['unresolved_external_paths']))
