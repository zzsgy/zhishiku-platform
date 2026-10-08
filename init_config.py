import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kb.settings')

import django
django.setup()

from core.models import SystemConfig, KnowledgeBase

# Default system settings
SystemConfig.objects.get_or_create(key='platform_name', defaults={'value': '智识库'})
SystemConfig.objects.get_or_create(key='theme', defaults={'value': 'light'})

# Defaults apply only to new rows; upgrades preserve user configuration.
SHOW_IN_KB = {'runtime': False, 'knowledge': True}
defaults = [
    ('运行档案库', 'runtime', '02_运行档案库'),
    ('知识库', 'knowledge', '03_知识库'),
]
for name, kind, directory in defaults:
    KnowledgeBase.objects.get_or_create(
        name=name,
        defaults={'kind': kind, 'directory': directory, 'show_in_kb': SHOW_IN_KB[kind],
                  'description': '系统搭建与运行档案（平台搭建文档 + 操作流水）' if kind == 'runtime' else ''}
    )

print('初始化完成')
