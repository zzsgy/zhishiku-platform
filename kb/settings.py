r"""
Django settings for the Knowledge Platform (v1).
Dev/standalone configuration. SQLite + local media.

Directory convention:
  C:\ZSK\XiTong   -> platform code, venv, db.sqlite3, static/media/runtime files
  C:\ZSK\ZhiShi   -> knowledge contents: build-library, runtime-archive, knowledge base
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 数据根目录（知识内容）与备份输出目录 —— 支持跨机便携运行
#
# 默认布局：<任意目录>/XiTong（本工程） 与 <任意目录>/ZhiShi（知识数据）同级。
#   * 本机：C:\ZSK\XiTong + C:\ZSK\ZhiShi —— 与旧版硬编码路径完全等价；
#   * 换机：把迁移包解压到 D:\Zhishiku，即成 D:\Zhishiku\XiTong + \ZhiShi，
#           无需改动任何配置即可原样运行（一键安装脚本依赖此约定）。
# 若需把知识数据放到别处，设环境变量 ZHISHIKU_DATA_ROOT 覆盖即可。
# ---------------------------------------------------------------------------
_env_data_root = os.environ.get('ZHISHIKU_DATA_ROOT', '').strip()
ZHI_SHI_ROOT = Path(_env_data_root) if _env_data_root else (BASE_DIR.parent / 'ZhiShi')

# 备份输出目录：刻意放在代码目录之外，避免「备份产物被下一次备份递归打包」。
_env_backup_root = os.environ.get('ZHISHIKU_BACKUP_ROOT', '').strip()
BACKUP_ROOT = Path(_env_backup_root) if _env_backup_root else (BASE_DIR.parent / '备份输出')
# 单个备份包允许的最大体积（字节），超出时中断并提示，防止磁盘被写满。
BACKUP_MAX_BYTES = int(os.environ.get('ZHISHIKU_BACKUP_MAX_BYTES', str(8 * 1024 ** 3)))

SECRET_KEY = 'dev-insecure-key-change-me-in-production-2026knowledge'
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'simpleui',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'import_export',
    'mdeditor',
    # project apps
    'core',
    'dashboard',
    'starmap',
    'wiki',
    'bookshelf',
    'knowledgebase',
    'inspiration',
    'selfmedia',
    'office',
    'collection',
    'runarchive',
    'systemsettings',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.OperationLogMiddleware',
]

ROOT_URLCONF = 'kb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context.platform_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'kb.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'static_collected'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Knowledge repository directory layout (under C:\ZSK\ZhiShi)
ZHI_SHI_DIRS = {
    'runtime_archive': ZHI_SHI_ROOT / '02_运行档案库',
    'knowledge': ZHI_SHI_ROOT / '03_知识库',
    'inbox': ZHI_SHI_ROOT / '04_临时中转',
    'archive': ZHI_SHI_ROOT / '05_归档库',
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# 运行日志
#
# 日志目录刻意不随备份包分发（打包时 logs/ 被排除），所以必须在启动时就地
# 创建。否则 Django 会在 logging.config.dictConfig 阶段直接抛
#     ValueError: Unable to configure handler 'file'
# 连 manage.py migrate 都跑不起来 —— 这是「解压即可用」的必要条件。
#
# 可用 ZHISHIKU_LOG_DIR 指定到别处；目录建不出来或不可写时，自动退化为
# 仅控制台日志，绝不因为日志问题让整个平台起不来。
# ---------------------------------------------------------------------------
_env_log_dir = os.environ.get('ZHISHIKU_LOG_DIR', '').strip()
LOG_DIR = Path(_env_log_dir) if _env_log_dir else (BASE_DIR / 'logs')
LOG_FILE = LOG_DIR / 'platform.log'
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8'):
        pass
    _LOG_FILE_OK = True
except OSError:
    _LOG_FILE_OK = False

_LOG_FORMATTERS = {
    'verbose': {'format': '[{asctime}] {levelname} {name}: {message}', 'style': '{'},
}
_LOG_HANDLERS = {
    'console': {'level': 'INFO', 'class': 'logging.StreamHandler', 'formatter': 'verbose'},
}
_LOG_HANDLER_NAMES = ['console']
if _LOG_FILE_OK:
    _LOG_HANDLERS['file'] = {
        'level': 'INFO',
        'class': 'logging.FileHandler',
        'filename': LOG_FILE,
        'encoding': 'utf-8',
        'formatter': 'verbose',
    }
    _LOG_HANDLER_NAMES = ['file', 'console']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': _LOG_FORMATTERS,
    'handlers': _LOG_HANDLERS,
    'loggers': {
        'kb': {'handlers': _LOG_HANDLER_NAMES, 'level': 'INFO', 'propagate': False},
        'core': {'handlers': _LOG_HANDLER_NAMES, 'level': 'INFO', 'propagate': False},
    },
}

# simpleui tweaks
SIMPLEUI_HOME_TITLE = '知识库平台'
SIMPLEUI_HOME_ICON = 'fa fa-book'

# mdeditor upload (within MEDIA_ROOT)
MDEDITOR_CONFIGS = {
    'default': {
        'width': '100%',
        'upload_image_formats': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'],
    }
}
