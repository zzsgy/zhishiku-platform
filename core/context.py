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

MODULE_TITLES = {
    'dashboard': '工作台', 'starmap': '关系图谱', 'wiki': 'Wiki 知识',
    'precipitation': '待整理知识', 'bookshelf': '书架', 'knowledgebase': '知识库',
    'notes': '随笔记', 'inspiration': '灵感库', 'selfmedia': '自媒体',
    'office': '办公中心', 'collection': '收集箱', 'runarchive': '活动记录',
    'settings': '系统设置', 'admin': '管理中心', 'accounts': '账号设置',
}


def platform_context(request):
    import hashlib
    path = request.path.strip('/').split('/')[0]
    active = MODULE_MAP.get(path, '')
    if request.path.startswith('/wiki/precipitation/'):
        active = 'precipitation'
    module_title = MODULE_TITLES.get(active or path, '知识工作台')
    return {
        'platform_name': SystemConfig.get_value('platform_name', '知识库平台'),
        'theme': SystemConfig.get_value('theme', 'light'),
        'active_module': active,
        'module_title': module_title,
        'zhi_shi_root': str(settings.ZHI_SHI_ROOT),
        'xi_tong_root': str(settings.BASE_DIR),
        'draft_namespace': hashlib.sha256((settings.SECRET_KEY + ':' + str(request.user.pk)).encode()).hexdigest()[:20],
    }
