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
        self._setup_sqlite_pragmas()

    @staticmethod
    def _setup_sqlite_pragmas():
        """SQLite 启用 WAL：服务进程 / worker / 管理后台多进程并发时，
        读写互不阻塞，避免「管理中心添加账号」等写操作撞上
        「其它操作正在保存」的 409 写冲突（DELETE 模式下的已知问题）。"""
        from django.db.backends.signals import connection_created

        def set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor != 'sqlite':
                return
            cursor = connection.cursor()
            try:
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=NORMAL')
            finally:
                cursor.close()

        connection_created.connect(set_sqlite_pragmas, dispatch_uid='core-sqlite-pragmas')
