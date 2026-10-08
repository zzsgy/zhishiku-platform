import mimetypes
from pathlib import Path
from django.conf import settings
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_not_required
from .storage import bounded_path


@login_not_required
@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
            cursor.execute('SELECT version, export_status FROM core_knowledgenode LIMIT 1')
            cursor.execute('SELECT id FROM core_job LIMIT 1')
    except Exception:
        return JsonResponse({'app': 'zhishiku-platform', 'status': 'unavailable'}, status=503)
    return JsonResponse({'app': 'zhishiku-platform', 'version': '1.1', 'status': 'ok'})


@require_GET
def media(request, path):
    try:
        file = bounded_path(settings.MEDIA_ROOT, path)
        if not file.is_file() or any(p.startswith('.') for p in Path(path).parts):
            raise Http404
        content_type = mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
        inline = content_type in {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'}
        response = FileResponse(open(file, 'rb'), as_attachment=not inline,
                                filename=file.name, content_type=content_type)
        response['Content-Security-Policy'] = "sandbox; default-src 'none'"
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except (ValueError, OSError):
        raise Http404
