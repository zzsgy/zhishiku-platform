"""一键安装/恢复脚本生成器。

产出的是**包内文件**（相对包根的路径 + 文本内容），由 backup_engine 写进 ZIP。

硬约束（本机血泪教训，务必遵守）：
  1. `.cmd` 内容必须是**纯 ASCII**。cmd.exe 在中文 Windows 上按码页 936 解析批处理，
     非 ASCII 注释会吞掉换行、把后续命令并进注释里（历史上就是这么坏掉的）。
     中文只允许出现在**文件名**里。
  2. `.cmd` 必须 **CRLF**。LF-only 会让 cmd 把多行并成一行。
  3. 脚本内部一切路径都由 `%~dp0` 推导，先 `cd /d "%~dp0"`，绝不写死绝对路径。
  4. 不隐藏窗口（`Start-Process -WindowStyle Hidden` 会被火绒拦截并杀进程）。
"""
from datetime import datetime

# 需要强制 CRLF 的后缀
_CRLF_SUFFIXES = ('.cmd', '.bat', '.ps1')


def _crlf(text):
    return text.replace('\r\n', '\n').replace('\n', '\r\n')


# ---------------------------------------------------------------------------
# 安装器：install/install.cmd
# ---------------------------------------------------------------------------
_INSTALL_CMD = r'''@echo off
rem ============================================================
rem  ZhiShiKu Knowledge Platform - one-click installer
rem
rem  ASCII-only + CRLF on purpose. cmd.exe mis-parses LF-only or
rem  non-ASCII batch files, so keep it that way when editing.
rem
rem  Steps:
rem    1. locate a USABLE Python 3.11 / 3.12    (validated, not just version-checked)
rem    2. create the virtual environment   (XiTong\venv)
rem    3. install dependencies             (XiTong\requirements.txt)
rem    4. prepare the database             (migrate + defaults)
rem    5. print a summary
rem
rem  Heavy optional extras (video / OCR / speech, several GB):
rem    install.cmd --full
rem
rem  Env switches:
rem    ZHISHIKU_PYTHON     full path to a python.exe to use
rem    ZHISHIKU_NO_MIRROR  1 = use the default PyPI index only
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title ZhiShiKu Installer

for %%I in ("%~dp0..") do set "PKG=%%~fI"
set "APP=%PKG%\XiTong"
set "DATA=%PKG%\ZhiShi"
set "VENV=%APP%\venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "REQ=%APP%\requirements.txt"
set "HASHOPT="
if exist "%APP%\requirements.lock" set "HASHOPT=--require-hashes"
if exist "%APP%\requirements.lock" set "REQ=%APP%\requirements.lock"
set "REQOPT=%APP%\requirements-optional.txt"
set "PY="
set "WITHOPT=0"
if /i "%~1"=="--full" set "WITHOPT=1"
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"
if /i "%ZHISHIKU_NO_MIRROR%"=="1" set "MIRROR="

echo ============================================================
echo   ZhiShiKu Knowledge Platform - one-click installer
echo ============================================================
echo   package : %PKG%
echo   program : %APP%
echo   data    : %DATA%
echo.

if not exist "%APP%\manage.py" goto :no_package
if not exist "%REQ%" goto :no_requirements

rem ---------- 1. locate a suitable Python ----------
rem  A candidate is accepted only when it is 3.11 or 3.12 AND can really create
rem  virtual environments (venv + ensurepip importable). Trimmed or
rem  relocated installs exist in the wild: a python.exe on PATH whose Lib
rem  folder is not on sys.path reports a perfectly good version yet dies at
rem  "python -m venv" with "No module named venv". So validate, and fall
rem  through to the next candidate instead of failing the whole install.
rem  Known-good versions are tried first (3.12 then 3.11) because the
rem  newest CPython may not have wheels for every pinned dependency yet.
echo [1/5] Looking for Python 3.11 or 3.12 ...
if defined ZHISHIKU_PYTHON call :probe_exe "%ZHISHIKU_PYTHON%"
if defined ZHISHIKU_PYTHON if not defined PY echo       [warn] ZHISHIKU_PYTHON is not usable - trying other interpreters
for %%V in (3.12 3.11) do if not defined PY call :probe_launcher %%V
if not defined PY call :probe_launcher 3
if not defined PY call :probe_cmd python
for %%D in ("%LOCALAPPDATA%\Programs\Python\Python313" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%LOCALAPPDATA%\Programs\Python\Python310" "C:\Python313" "C:\Python312" "C:\Python311" "C:\Python310") do if not defined PY call :probe_exe "%%~D\python.exe"
if not defined PY goto :no_python
echo       using : %PY%
"%PY%" -V

rem ---------- 2. virtual environment ----------
echo.
echo [2/5] Preparing the virtual environment ...
if exist "%PYEXE%" goto :venv_ready
"%PY%" -m venv "%VENV%"
if errorlevel 1 goto :venv_fail
echo       created : %VENV%
goto :venv_done
:venv_ready
echo       already present, reusing it
:venv_done
if not exist "%PYEXE%" goto :venv_fail
"%PYEXE%" -X utf8 -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1

rem ---------- 3. dependencies ----------
echo.
echo [3/5] Installing dependencies - this may take a few minutes ...
set "PIPOK=1"
if defined MIRROR "%PYEXE%" -X utf8 -m pip install %HASHOPT% -r "%REQ%" %MIRROR% --disable-pip-version-check
if defined MIRROR if errorlevel 1 set "PIPOK=0"
if not defined MIRROR set "PIPOK=0"
if "%PIPOK%"=="0" (
  echo       mirror skipped or failed, using the default PyPI index ...
  "%PYEXE%" -X utf8 -m pip install %HASHOPT% -r "%REQ%" --disable-pip-version-check
)
if errorlevel 1 goto :pip_fail

if "%WITHOPT%"=="1" goto :opt_deps
if exist "%REQOPT%" echo       optional extras NOT installed - see the guide if you need video/OCR
goto :db
:opt_deps
echo       installing optional extras - video / OCR / speech ...
"%PYEXE%" -X utf8 -m pip install -r "%REQOPT%" --disable-pip-version-check
if errorlevel 1 echo       [warn] optional extras failed - the platform still runs without them

rem ---------- 4. database ----------
:db
echo.
echo [4/5] Preparing the database ...
if exist "%APP%\db.sqlite3" goto :db_keep
echo       no database found - creating an empty one
goto :db_migrate
:db_keep
echo       existing database found - keeping all of its records
:db_migrate
"%PYEXE%" -X utf8 "%APP%\manage.py" migrate --noinput
if errorlevel 1 goto :migrate_fail
"%PYEXE%" -X utf8 "%APP%\init_config.py"
if errorlevel 1 goto :migrate_fail

rem Authentication is mandatory; no default password is shipped.
"%PYEXE%" -X utf8 "%APP%\manage.py" bootstrap_account
if errorlevel 1 goto :migrate_fail

rem ---------- 5. summary ----------
echo.
echo [5/5] Done. Current database summary:
"%PYEXE%" "%PKG%\install\stats.py" "%APP%"
echo.
echo ============================================================
echo   INSTALLATION COMPLETE
echo ============================================================
echo   Start the platform with the launcher in the package root,
echo   or from a console:
echo       install\start-platform.cmd
echo.
echo   Address        : http://127.0.0.1:8000/
echo   Knowledge data : %DATA%
echo   Chinese guide  : the .md file in the package root
echo ============================================================
echo.
pause
exit /b 0

:no_package
echo [ERROR] %APP%\manage.py was not found.
echo         Extract the WHOLE package first, then run this script from
echo         inside the extracted folder - never run it from inside the zip.
echo.
pause
exit /b 1

:no_requirements
echo [ERROR] %REQ% was not found - the package looks incomplete.
echo.
pause
exit /b 1

:no_python
echo [ERROR] No usable Python 3.11 or 3.12 was found on this computer.
echo.
echo   "Usable" means version 3.11 or 3.12 AND the venv module is present.
echo   Some trimmed / relocated installs are missing it, so a python.exe
echo   that answers "python -V" can still be rejected here.
echo.
echo   Install a full build from
echo       https://www.python.org/downloads/windows/
echo   and TICK "Add python.exe to PATH" during setup, then run this
echo   script again.
echo.
echo   Already installed somewhere unusual? Point this script at it:
echo       set ZHISHIKU_PYTHON=C:\path\to\python.exe
echo.
pause
exit /b 1

:venv_fail
echo [ERROR] Could not create the virtual environment at
echo         %VENV%
echo.
pause
exit /b 1

:pip_fail
echo [ERROR] Dependency installation failed. Check the network, or set
echo         ZHISHIKU_NO_MIRROR=1 to retry on the default PyPI index.
echo.
pause
exit /b 1

:migrate_fail
echo [ERROR] Database preparation failed. See the messages above.
echo.
pause
exit /b 1

rem ============================================================
rem  helpers - interpreter probing
rem
rem  Every helper leaves PY unset on failure so the caller can move on
rem  to the next candidate. They must stay ABOVE the final exit /b, which
rem  is why they live after all the user-facing error labels: the normal
rem  flow never falls through into them.
rem  The validation imports venv and ensurepip on purpose - see step 1.
rem ============================================================
:probe_exe
rem %~1 = full path to a python.exe
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys,venv,ensurepip;sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,13) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0

:probe_launcher
rem %~1 = version spec handed to the py launcher, e.g. 3.13 or 3
if "%~1"=="" exit /b 0
for /f "delims=" %%i in ('py -%~1 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY call :probe_exe "%%i"
exit /b 0

:probe_cmd
rem %~1 = interpreter executable looked up on PATH, e.g. python
if "%~1"=="" exit /b 0
for /f "delims=" %%i in ('%~1 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY call :probe_exe "%%i"
exit /b 0
'''


# ---------------------------------------------------------------------------
# 启动器：install/start-platform.cmd
# ---------------------------------------------------------------------------
_START_CMD = r'''@echo off
rem Launch the platform (delegates to the project's own launcher).
rem ASCII-only + CRLF on purpose.
for %%I in ("%~dp0..") do set "PKG=%%~fI"
if not exist "%PKG%\XiTong\start-zhishiku.cmd" (
  echo [ERROR] %PKG%\XiTong\start-zhishiku.cmd not found.
  echo         Run install\install.cmd first.
  echo.
  pause
  exit /b 1
)
call "%PKG%\XiTong\start-zhishiku.cmd"
exit /b %errorlevel%
'''


# ---------------------------------------------------------------------------
# 统计脚本：install/stats.py
# ---------------------------------------------------------------------------
_STATS_PY = '''"""Print a short summary of the platform database.

Called by install/install.cmd at the end of the installation so the user can
see whether the database came up EMPTY (system backup) or already carries the
knowledge records (migration backup).

The verdict deliberately ignores KnowledgeBase / SystemConfig rows: a freshly
installed platform always has those, because init_config.py creates the two
default libraries (run-archive + knowledge-base) and the default settings.
Counting them made a blank system package look like a migration package.
Only real knowledge CONTENT decides.

Usage:  python stats.py [<path to the XiTong folder>]
"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
app = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'XiTong')
app = os.path.abspath(app)

if not os.path.isdir(app):
    print('    (platform folder not found: %s)' % app)
    raise SystemExit(1)

sys.path.insert(0, app)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kb.settings')

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402

# (label, app_label, model) - real knowledge content, decides the verdict.
CONTENT = [
    ('knowledge nodes', 'core', 'KnowledgeNode'),
    ('node notes', 'core', 'NodeNote'),
    ('node links', 'core', 'Edge'),
    ('collection items', 'core', 'CollectionItem'),
    ('link-library items', 'core', 'LinkItem'),
    ('work files', 'core', 'WorkFileIndex'),
    ('books', 'bookshelf', 'Book'),
    ('reading notes', 'bookshelf', 'ReadingNote'),
    ('golden sentences', 'bookshelf', 'GoldenSentence'),
    ('inspirations', 'inspiration', 'Inspiration'),
    ('inspiration notes', 'inspiration', 'NoteLog'),
    ('work records', 'office', 'WorkRecord'),
    ('advises', 'office', 'Advise'),
    ('reports', 'office', 'Report'),
]

# Structural / activity rows: shown for context, never part of the verdict.
INFO = [
    ('knowledge bases', 'core', 'KnowledgeBase'),
    ('config entries', 'core', 'SystemConfig'),
    ('operation logs', 'core', 'OperationLog'),
]


def count(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name).objects.count()
    except LookupError:
        return None


print('    Knowledge content:')
total = 0
any_nonzero = False
for label, app_label, model_name in CONTENT:
    n = count(app_label, model_name)
    if n is None:
        continue
    total += n
    if n:
        any_nonzero = True
        print('      %-21s: %d' % (label, n))
if not any_nonzero:
    print('      (none)')

print('    Structure / activity (not counted):')
for label, app_label, model_name in INFO:
    n = count(app_label, model_name)
    if n is not None:
        print('      %-21s: %d' % (label, n))

print('      %-21s: %d' % ('content rows total', total))
print('')
if total == 0:
    print('    -> BLANK platform: no knowledge content. (SYSTEM backup)')
else:
    print('    -> knowledge content found: %d rows. (MIGRATION backup)' % total)
'''


# ---------------------------------------------------------------------------
# 根级中文入口（内容仍是纯 ASCII，只靠文件名对用户友好）
# ---------------------------------------------------------------------------
_ENTRY_INSTALL = r'''@echo off
rem Double-click entry: run the installer. Content is ASCII-only by design;
rem the Chinese name is what makes it obvious for users.
call "%~dp0install\install.cmd" %*
exit /b %errorlevel%
'''

_ENTRY_START = r'''@echo off
rem Double-click entry: start the platform.
call "%~dp0install\start-platform.cmd" %*
exit /b %errorlevel%
'''


# ---------------------------------------------------------------------------
# 知识恢复脚本：恢复知识.cmd
# ---------------------------------------------------------------------------
_RESTORE_CMD = r'''@echo off
rem Restore only through the verified command, into a NEW directory.
echo Run this command from an installed platform's virtual environment:
echo   python -X utf8 manage.py restore_backup "BACKUP.zip" --destination "NEW_FOLDER"
echo The original ZIP is required. Existing destinations are rejected.
echo Check the restored data before switching installations.
exit /b 1
'''



# ---------------------------------------------------------------------------
# 中文安装说明
# ---------------------------------------------------------------------------
def _install_guide(pkg):
    return '''# 安装与恢复说明 · {pkg}

本包面向个人本机 Windows 使用。本次实际验证环境为 Windows、Python 3.11 与 3.12；其它系统与可选视频/OCR 工具未完成发行验收。

## 先区分用途

| 包类型 | 正文与原件 | 数据库/账号 | 用法 |
|---|---|---|---|
| 系统包 | 空数据骨架 | 建新库、创建新管理员 | 完整解压后安装 |
| 知识包 | 原件与知识导出 | 知识 fixture，不含原账号 | 从已安装的可信平台执行隔离恢复 |
| 迁移包 | 原件与知识导出 | 一致快照、保留账号、移除 API 凭据 | 优先从可信平台执行隔离恢复后再安装 |

原件与正文可能含个人资料；脱敏 API 配置不代表备份没有敏感内容。本包未加密，也没有数字签名，只对文件完整性做哈希校验。仅恢复自己可信的包。

## 新平台安装

1. 完整解压，保留 XiTong 与 ZhiShi 的同级结构，不在 ZIP 内启动，也不向旧平台目录覆盖解压。中文与空格路径已完成核心恢复测试；建议避免过深目录。
2. 安装完整的 Python 3.11 或 3.12（含 venv 与 ensurepip），双击「一键安装.cmd」。可通过 ZHISHIKU_PYTHON 指定解释器。
3. 安装器建立独立虚拟环境，优先使用带哈希的 requirements.lock，执行数据库迁移和默认项初始化。初始化保留已有配置，没有出厂默认密码；没有管理员时会要求创建。
4. 双击「启动平台.cmd」；登录后使用。启动器启动本机 Waitress 服务及独立任务 worker，健康检查通过后才打开浏览器。

安装需要联网下载依赖，默认尝试清华镜像；ZHISHIKU_NO_MIRROR=1 可使用官方 PyPI。升级依赖前应审计新锁文件，勿把随意的 pip upgrade 当作发行流程。

端口被其它进程占用时启动器报错，不会自动结束它。可执行 XiTong/launch-platform.ps1 -Port 8010。停止只处理本平台记录且身份核对通过的进程。运行日志位于 XiTong/.runtime 和 XiTong/logs。

## 迁移与知识恢复

保留原始 ZIP。在已安装的可信版本目录执行：

```
venv/Scripts/python.exe -X utf8 manage.py verify_backup "BACKUP.zip"
venv/Scripts/python.exe -X utf8 manage.py restore_backup "BACKUP.zip" --destination "D:/新平台目录"
```

目标必须尚不存在。恢复先核对清单、哈希和路径，在临时目录迁移/导入并检查数据库完整性与外键，通过后才发布目标目录。遇到错误即停止；旧格式备份需保留原件并单独核对，不直接套用新恢复器。

打开新目录中的「恢复验证.json」，处理外部工作目录提示。当前恢复重定位已知原件/书籍/报告等路径；外部磁盘目录不猜测搬迁位置。创建新的虚拟环境，重配 AI/Gitee 凭据；知识/系统包需创建管理员，迁移包保留原账号。确认书籍、原件、知识、报告等样例可用后，再手工切换到新平台。

## 可选工具

视频、OCR、语音转写默认不安装。install/install.cmd --full 会请求安装 requirements-optional.txt，并可能下载数 GB 的环境/模型。该清单尚未锁定或完成本次漏洞审计。还需单独配置 FFmpeg、Tesseract、Whisper；不随源码分发外部二进制。缺少工具应查看原件与任务诊断。

## 数据与权限

默认 XiTong/db.sqlite3 保存账号与业务记录，XiTong/media 保存上传原件，同级 ZhiShi 保存可重建正文导出及办公产物，同级「备份输出」保存备份。可用 ZHISHIKU_DB_PATH、ZHISHIKU_MEDIA_ROOT、ZHISHIKU_DATA_ROOT、ZHISHIKU_BACKUP_ROOT 覆盖。

业务页面必须登录，设置与备份需要管理员。云端 AI 资料外发默认关闭；需要时由管理员明确开启。默认不会自动把失败请求转给其它 AI 服务。本机无密钥模型须显式配置为本机服务。

当前不是现成的多人隔离/跨电脑同步平台。网络部署需要独立密钥、明确主机和 HTTPS 等额外配置，详见 XiTong/docs/OPERATIONS.md。
'''.replace('{pkg}', pkg)


def generated_files(kind, pkg_name=''):
    """返回 [(包内相对路径, 文本内容)]。

    kind: knowledge / system / migration
    pkg_name: 包名（用于说明文档标题；扫描阶段可留空）
    """
    out = []
    if kind == 'knowledge':
        out.append(('恢复知识.cmd', _crlf(_RESTORE_CMD)))
        return out

    # system / migration
    out.append(('install/install.cmd', _crlf(_INSTALL_CMD)))
    out.append(('install/start-platform.cmd', _crlf(_START_CMD)))
    out.append(('install/stats.py', _crlf(_STATS_PY)))
    out.append(('一键安装.cmd', _crlf(_ENTRY_INSTALL)))
    out.append(('启动平台.cmd', _crlf(_ENTRY_START)))
    out.append(('安装说明.md', _crlf(_install_guide(pkg_name or '<package>'))))
    out.append(('VERSION.txt', _version_txt(kind, pkg_name)))
    return out


def _version_txt(kind, pkg_name):
    lines = [
        'ZhiShiKu Knowledge Platform - backup package',
        '============================================',
        'package name : %s' % (pkg_name or '(pending)'),
        'backup kind  : %s' % kind,
        'created at   : %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        '',
        'Platform stack: Django 5.2 + SQLite + Bootstrap 5 / ECharts / D3',
        'Runtime need  : Python 3.11 / 3.12  (see install/install.cmd)',
        '',
        'Entry points:',
        '  install/install.cmd          one-click installer',
        '  install/start-platform.cmd   start the platform',
        '',
        'See the Chinese guide (.md in the package root) for details.',
    ]
    return _crlf('\n'.join(lines) + '\n')
