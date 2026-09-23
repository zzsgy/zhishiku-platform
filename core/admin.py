from django.contrib import admin
from .models import KnowledgeBase, KnowledgeNode, Edge, CollectionItem, OperationLog, SystemConfig

admin.site.register(KnowledgeBase)
admin.site.register(KnowledgeNode)
admin.site.register(Edge)
admin.site.register(CollectionItem)
admin.site.register(OperationLog)
admin.site.register(SystemConfig)
