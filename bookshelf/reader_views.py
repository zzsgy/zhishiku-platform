import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST, require_safe
from .models import Book, ReaderPosition
from .reader import original, preview, docx_image


@lru_cache(maxsize=128)
def _fingerprint(path, size, mtime):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_key(book):
    path = original(book)
    stat = path.stat()
    return _fingerprint(str(path), stat.st_size, stat.st_mtime_ns)


def reader_config(book, user):
    try:
        key = file_key(book)
    except (ValueError, OSError):
        key = ''
    position = ReaderPosition.objects.filter(book=book, user=user, file_key=key).first() if key else None
    return {'book': book.pk, 'format': book.document_format, 'fileKey': key,
            'page': position.page if position else max(1, book.progress),
            'offset': position.offset if position else 0,
            'percent': position.percent if position else 0,
            'preferences': position.preferences if position else {},
            'hasPosition': bool(position), 'available': bool(key),
            'fileUrl': f'/bookshelf/book/{book.pk}/original/',
            'previewUrl': f'/bookshelf/book/{book.pk}/preview/',
            'positionUrl': f'/bookshelf/book/{book.pk}/position/'}


@require_GET
def document_preview(request, pk):
    book = get_object_or_404(Book, pk=pk)
    try:
        return JsonResponse({'ok': True, **preview(book)})
    except (ValueError, OSError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=422)
    except Exception:
        import logging
        logging.getLogger('kb').exception('Reader preview failed for book %s', pk)
        return JsonResponse({'ok': False, 'error': '正文预览未能完成，请下载原件核对。'}, status=422)


@require_safe
def original_document(request, pk):
    book = get_object_or_404(Book, pk=pk)
    try:
        path = original(book)
    except (ValueError, OSError):
        raise Http404
    name = book.asset.original_name if book.asset else path.name
    size = path.stat().st_size
    pdf = path.suffix.lower() == '.pdf'
    mime = 'application/pdf' if pdf else 'application/octet-stream'
    header = request.headers.get('Range', '') if pdf and request.GET.get('download') != '1' else ''
    if header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
        try:
            if not match or not any(match.groups()):
                raise ValueError
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            if start >= size or end < start:
                raise ValueError
        except ValueError:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response

        def chunks():
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        response = StreamingHttpResponse(chunks() if request.method != 'HEAD' else (), status=206, content_type=mime)
        response['Content-Length'] = str(end - start + 1)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    else:
        response = FileResponse(path.open('rb'), as_attachment=not pdf or request.GET.get('download') == '1',
                                filename=name, content_type=mime)
        if request.method == 'HEAD':
            response.close()
            response.streaming_content = ()
    if pdf:
        response['Accept-Ranges'] = 'bytes'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_GET
def document_image(request, pk, rid):
    book = get_object_or_404(Book, pk=pk)
    try:
        blob, mime = docx_image(book, rid)
    except Exception:
        raise Http404
    response = HttpResponse(blob, content_type=mime)
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_POST
def save_position(request, pk):
    book = get_object_or_404(Book, pk=pk)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(data.get('page', 1), int):
            raise ValueError
        page = int(data.get('page', 1))
        offset = float(data.get('offset', 0))
        percent = float(data.get('percent', 0))
        if isinstance(data.get('page'), bool) or not 1 <= page <= 100000:
            raise ValueError
        if not math.isfinite(offset) or not 0 <= offset <= 1 or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError
        key = file_key(book)
        if data.get('fileKey') != key:
            return JsonResponse({'ok': False, 'error': '文档已更新，请刷新阅读页后继续。'}, status=409)
        preferences = {}
        for field, low, high in [('fontSize', 14, 26), ('lineHeight', 1.4, 2.4), ('width', 560, 1100)]:
            if field in data:
                value = float(data[field])
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError
                preferences[field] = value
        if data.get('paper') in {'auto', 'white', 'warm', 'dark'}:
            preferences['paper'] = data['paper']
        obj, created = ReaderPosition.objects.get_or_create(book=book, user=request.user,
            defaults={'file_key': key})
        obj.file_key, obj.page, obj.offset, obj.percent = key, page, offset, percent
        obj.preferences = {**obj.preferences, **preferences}
        obj.save()
        return JsonResponse({'ok': True})
    except (ValueError, TypeError, OSError, OverflowError):
        return JsonResponse({'ok': False, 'error': '阅读位置或设置无效。'}, status=400)
