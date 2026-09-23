import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kb.settings')

import django
django.setup()

from core.models import SystemConfig, KnowledgeBase

# Default system settings
SystemConfig.set_value('platform_name', '智识库')
SystemConfig.set_value('theme', 'light')

# Default knowledge bases mapped to C:\ZSK\ZhiShi directories
SHOW_IN_KB = {'runtime': False, 'knowledge': True}
defaults = [
    ('运行档案库', 'runtime', '02_运行档案库'),
    ('知识库', 'knowledge', '03_知识库'),
]
for name, kind, directory in defaults:
    KnowledgeBase.objects.get_or_create(
        name=name,
        defaults={'kind': kind, 'directory': directory, 'show_in_kb': SHOW_IN_KB[kind]}
    )
# 防御性 ensure：已存在记录可能因字段回填为 True，统一纠正展示开关
for kind, show in SHOW_IN_KB.items():
    KnowledgeBase.objects.filter(kind=kind).update(show_in_kb=show)
# 运行档案库已整合原「平台搭建库」职责：系统搭建文档 + 操作流水
KnowledgeBase.objects.filter(kind='runtime').update(
    description='系统搭建与运行档案（平台搭建文档 + 操作流水）'
)

print('初始化完成')
