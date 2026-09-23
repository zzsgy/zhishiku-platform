"""运行档案：把工作台每一次访问与操作写入 OperationLog。

排除静态资源 / admin / 媒体文件，避免噪声。流水线模式，不阻塞响应。
"""
from .services import log_operation

SKIP_PREFIXES = ('/static/', '/media/', '/admin/', '/favicon', '/django', '/runarchive/')


class OperationLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return self.get_response(request)

        module = path.strip('/').split('/')[0] or '总览'
        method = request.method
        action = 'view' if method == 'GET' else f'post'

        response = self.get_response(request)

        # 仅记录 2xx/3xx 的成功访问，且排除 HEAD 等
        if response.status_code < 400:
            try:
                detail = f'{method} {path}'
                if method != 'GET':
                    detail += f' -> {response.status_code}'
                log_operation(module, action, detail=detail)
            except Exception:
                pass
        return response
