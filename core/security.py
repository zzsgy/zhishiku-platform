"""Access and input boundaries shared by every business view."""
import json
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse


def error(message, status=400, code='invalid_input'):
    return JsonResponse({'ok': False, 'error': message, 'code': code}, status=status)


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view, args, kwargs):
        if request.path.startswith(('/accounts/', '/admin/', '/static/')) or request.path == '/health/':
            return None
        if not request.user.is_authenticated:
            if request.method not in ('GET', 'HEAD') or request.content_type == 'application/json':
                return error('请先登录', 401, 'authentication_required')
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if request.path.startswith('/settings/') and not request.user.is_staff:
            return error('此操作需要管理员权限', 403, 'staff_required')
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                return error('请求体必须是有效 JSON 对象')
            if not isinstance(data, dict):
                return error('请求体必须是 JSON 对象')
            for key, value in data.items():
                if key == 'sources':
                    if not isinstance(value, list) or len(value) > 20 or any(
                        not isinstance(v, dict) or not isinstance(v.get('type'), str)
                        or not isinstance(v.get('ref'), (str, int)) for v in value):
                        return error('来源必须是有效对象列表，最多 20 项')
                    if any(str(v['ref']).isdigit() is False or isinstance(v['ref'], bool) for v in value):
                        return error('来源记录必须使用有效的数字编号')
                elif key == 'options':
                    if not isinstance(value, dict) or any(not isinstance(v, bool) for v in value.values()):
                        return error('选项必须是布尔值对象')
                elif value is not None and not isinstance(value, (str, int, float, bool)):
                    return error(f'{key} 的类型不正确')
                elif key in {'text', 'note', 'question', 'title', 'provider', 'url', 'mode',
                             'field', 'value', 'kind', 'name', 'api_key', 'model', 'base_url', 'type',
                             'category', 'tags', 'source', 'location', 'platform', 'date'} and value is not None and not isinstance(value, str):
                    return error(f'{key} 必须是文本')
        data = data if request.content_type == 'application/json' else (request.GET if request.method == 'GET' else request.POST)
        for key in ('id', 'book', 'node', 'base', 'parent', 'progress', 'total_pages', 'version'):
            if key == 'id' and request.path.startswith('/settings/ai/'):
                continue  # Provider IDs are strings; business object IDs are numeric.
            value = data.get(key)
            if value not in (None, ''):
                try:
                    number = int(value)
                    if isinstance(value, bool) or number < 0 or number > 2**63-1 or str(number) != str(value):
                        raise ValueError
                except (ValueError, TypeError):
                    return error(f'{key} 必须是非负整数')
        if any(upload.size > settings.UPLOAD_MAX_BYTES for upload in request.FILES.values()):
            return error('原件超过上传大小限制', 413, 'upload_too_large')
        return None

    def __call__(self, request):
        response = self.get_response(request)
        if isinstance(response, JsonResponse) and response.status_code == 200:
            try:
                if json.loads(response.content).get('ok') is False:
                    response.status_code = 400
            except (ValueError, AttributeError):
                pass
        # SimpleUI removes Django's middleware at startup; protect business responses here.
        response.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.setdefault('X-Content-Type-Options', 'nosniff')
        if request.user.is_authenticated:
            response.setdefault('Cache-Control', 'private, no-store')
        return response

    def process_exception(self, request, exception):
        from .revisions import VersionConflict
        from django.core.exceptions import ValidationError
        if isinstance(exception, VersionConflict):
            return error(str(exception), 409, 'version_conflict')
        if isinstance(exception, ValidationError):
            return error('；'.join(exception.messages), 422, 'validation_failed')
        from django.db import OperationalError
        if isinstance(exception, OperationalError) and 'locked' in str(exception).lower():
            return error('其它操作正在保存，请核对页面后重试', 409, 'write_conflict')
