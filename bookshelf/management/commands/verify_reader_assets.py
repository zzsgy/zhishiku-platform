"""Release/startup check: all pinned runtime assets must survive packaging."""
import hashlib
import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '核对阅读器的本地组件完整性'

    def handle(self, *args, **options):
        root = settings.BASE_DIR / 'static/vendor/pdfjs-6.4.299'
        try:
            manifest = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
            for relative, expected in manifest['files'].items():
                path = (root / relative).resolve()
                if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(relative)
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError('阅读器组件不完整，请重新同步完整代码包。') from exc
        self.stdout.write('阅读器本地组件完整性验证通过。')
