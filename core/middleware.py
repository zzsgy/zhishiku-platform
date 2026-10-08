"""Request identity is separate from explicit business audit events."""
from contextvars import ContextVar
import uuid
actor = ContextVar('zhishiku_actor', default='系统')
class OperationLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        token = actor.set(request.user.get_username() if request.user.is_authenticated else '匿名')
        try:
            response = self.get_response(request)
            response['X-Request-ID'] = request.request_id
            return response
        finally:
            actor.reset(token)
