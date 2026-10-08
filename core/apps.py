from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = '核心数据'

    def ready(self):
        from django.conf import settings
        middleware = 'django.middleware.clickjacking.XFrameOptionsMiddleware'
        # SimpleUI.ready removes this globally; its same-origin iframe still works.
        if middleware not in settings.MIDDLEWARE:
            settings.MIDDLEWARE.append(middleware)
