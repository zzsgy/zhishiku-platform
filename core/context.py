"""Template context processor: expose platform name, theme, active module."""
from django.conf import settings
from .models import SystemConfig


MODULE_MAP = {
    '': 'dashboard',
    'starmap': 'starmap',
    'wiki': 'wiki',
    'bookshelf': 'bookshelf',
    'knowledgebase': 'knowledgebase',
    'notes': 'notes',
    'inspiration': 'inspiration',
    'selfmedia': 'selfmedia',
    'office': 'office',
    'collection': 'collection',
    'runarchive': 'runarchive',
    'settings': 'settings',
}


def platform_context(request):
    path = request.path.strip('/').split('/')[0]
    active = MODULE_MAP.get(path, '')
    return {
        'platform_name': SystemConfig.get_value('platform_name', '知识库平台'),
        'theme': SystemConfig.get_value('theme', 'light'),
        'active_module': active,
        'zhi_shi_root': str(settings.ZHI_SHI_ROOT),
        'xi_tong_root': str(settings.BASE_DIR),
    }
