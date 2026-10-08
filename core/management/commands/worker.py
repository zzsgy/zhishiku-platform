import time
from django.core.management.base import BaseCommand
from core.jobs import run_one


class Command(BaseCommand):
    help = '执行持久化任务；个人版启动一个 worker 即可'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        while True:
            worked = run_one()
            if options['once']:
                return
            if not worked:
                time.sleep(1)
