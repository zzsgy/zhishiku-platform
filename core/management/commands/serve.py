from django.core.management.base import BaseCommand
from django.core.wsgi import get_wsgi_application


class Command(BaseCommand):
    help = '以 Waitress 启动本机发行服务（默认只监听回环）'
    def add_arguments(self, parser):
        parser.add_argument('--port', type=int, default=8000)
        parser.add_argument('--host', default='127.0.0.1')
    def handle(self, *args, **options):
        from waitress import serve
        from django.conf import settings
        serve(get_wsgi_application(), host=options['host'], port=options['port'],
              threads=4, max_request_body_size=settings.UPLOAD_MAX_BYTES + 1024**2)
