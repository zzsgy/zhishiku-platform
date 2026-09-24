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
rem    1. locate a USABLE Python 3.10+    (validated, not just version-checked)
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
rem  A candidate is accepted only when it is 3.10+ AND can really create
rem  virtual environments (venv + ensurepip importable). Trimmed or
rem  relocated installs exist in the wild: a python.exe on PATH whose Lib
rem  folder is not on sys.path reports a perfectly good version yet dies at
rem  "python -m venv" with "No module named venv". So validate, and fall
rem  through to the next candidate instead of failing the whole install.
rem  Known-good versions are tried first (3.13 down to 3.10) because the
rem  newest CPython may not have wheels for every pinned dependency yet.
echo [1/5] Looking for Python 3.10 or newer ...
if defined ZHISHIKU_PYTHON call :probe_exe "%ZHISHIKU_PYTHON%"
if defined ZHISHIKU_PYTHON if not defined PY echo       [warn] ZHISHIKU_PYTHON is not usable - trying other interpreters
for %%V in (3.13 3.12 3.11 3.10) do if not defined PY call :probe_launcher %%V
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
"%PYEXE%" -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>&1

rem ---------- 3. dependencies ----------
echo.
echo [3/5] Installing dependencies - this may take a few minutes ...
set "PIPOK=1"
if defined MIRROR "%PYEXE%" -m pip install -r "%REQ%" %MIRROR% --disable-pip-version-check
if defined MIRROR if errorlevel 1 set "PIPOK=0"
if not defined MIRROR set "PIPOK=0"
if "%PIPOK%"=="0" (
  echo       mirror skipped or failed, using the default PyPI index ...
  "%PYEXE%" -m pip install -r "%REQ%" --disable-pip-version-check
)
if errorlevel 1 goto :pip_fail

if "%WITHOPT%"=="1" goto :opt_deps
if exist "%REQOPT%" echo       optional extras NOT installed - see the guide if you need video/OCR
goto :db
:opt_deps
echo       installing optional extras - video / OCR / speech ...
"%PYEXE%" -m pip install -r "%REQOPT%" --disable-pip-version-check
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
"%PYEXE%" "%APP%\manage.py" migrate --noinput
if errorlevel 1 goto :migrate_fail
"%PYEXE%" "%APP%\init_config.py"
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
echo [ERROR] No usable Python 3.10 or newer was found on this computer.
echo.
echo   "Usable" means version 3.10+ AND the venv module is present.
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
"%~1" -c "import sys,venv,ensurepip;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
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
rem ============================================================
rem  ZhiShiKu - restore knowledge data from this backup package
rem
rem  Copies the knowledge folders back into a platform installation.
rem  Nothing is deleted: files that already exist are overwritten only
rem  when they have the same name, everything else is left untouched.
rem
rem  ASCII-only + CRLF on purpose.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title ZhiShiKu - Restore knowledge data

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
set "TARGET="
set "LOADJSON=0"
if /i "%~1"=="--load-json" set "LOADJSON=1"

echo ============================================================
echo   ZhiShiKu - restore knowledge data
echo ============================================================
echo   backup  : %HERE%
echo.

rem --- auto-detect: bundle sitting right next to (or inside) the platform ---
if exist "%HERE%\..\XiTong\manage.py" for %%I in ("%HERE%\..") do set "TARGET=%%~fI"
if not defined TARGET if exist "%HERE%\XiTong\manage.py" for %%I in ("%HERE%") do set "TARGET=%%~fI"

if defined TARGET goto :have_target
echo   Where should the data go? Enter the folder that CONTAINS the
echo   platform - that is, the parent of the XiTong folder.
echo   Example: C:\ZSK
echo.
set /p "TARGET=Target folder: "
if not defined TARGET goto :cancel

:have_target
if not exist "%TARGET%\XiTong\manage.py" goto :bad_target
echo   target  : %TARGET%
echo.
echo   About to copy:
echo     ZhiShi   ->  %TARGET%\ZhiShi
echo     media    ->  %TARGET%\XiTong\media
echo.
echo   Press Ctrl+C to abort, or
pause

if not exist "%HERE%\ZhiShi" goto :skip_zhishi
echo [..] copying knowledge folders ...
xcopy "%HERE%\ZhiShi" "%TARGET%\ZhiShi" /E /I /Y /Q
if errorlevel 1 echo       [warn] some knowledge files could not be copied
:skip_zhishi

if not exist "%HERE%\media" goto :skip_media
echo [..] copying uploaded media ...
xcopy "%HERE%\media" "%TARGET%\XiTong\media" /E /I /Y /Q
if errorlevel 1 echo       [warn] some media files could not be copied
:skip_media

if "%LOADJSON%"=="1" goto :load_json
echo.
echo   File restore finished.
echo   The database records (notes, tags, links, reading progress ...) live in
echo   database\*.json. Importing them is only needed when the platform
echo   database itself was lost. Re-run with --load-json to do that.
goto :done

:load_json
echo.
echo [..] importing database records from the JSON export ...
set "JSONFILE="
for %%F in ("%HERE%\database\*.json") do set "JSONFILE=%%~fF"
if not defined JSONFILE goto :no_json
if not exist "%TARGET%\XiTong\venv\Scripts\python.exe" goto :no_venv
"%TARGET%\XiTong\venv\Scripts\python.exe" "%TARGET%\XiTong\manage.py" loaddata "%JSONFILE%"
if errorlevel 1 echo       [warn] importing database records failed
if not errorlevel 1 echo       imported %JSONFILE%
goto :done

:no_json
echo       [warn] database\*.json not found in this package - skipped
goto :done

:no_venv
echo       [warn] the platform virtual environment is missing:
echo              %TARGET%\XiTong\venv\Scripts\python.exe
echo              Install the platform first, then run this script again.
goto :done

:done
echo.
echo ============================================================
echo   KNOWLEDGE RESTORE FINISHED
echo ============================================================
echo   Start the platform and check the knowledge sections
echo   (knowledge base / WiKI / bookshelf / office platform).
echo ============================================================
echo.
pause
exit /b 0

:bad_target
echo [ERROR] No platform found under:
echo         %TARGET%\XiTong\manage.py
echo         Please point at the folder that contains the XiTong folder.
echo.
pause
exit /b 1

:cancel
echo Aborted - nothing was changed.
pause
exit /b 1
'''


# ---------------------------------------------------------------------------
# 中文安装说明
# ---------------------------------------------------------------------------
def _install_guide(pkg):
    return f'''# 安装说明 · {pkg}

这个包是**空运行平台**或**迁移整包**，双击根目录下的「一键安装.cmd」即可完成安装。

## 一、安装前请确认

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（64 位） |
| Python | **3.10 或更高**（推荐 3.11 ~ 3.13） |
| 网络 | 安装依赖需要联网（脚本会用清华镜像，失败时自动回退官方源） |
| 磁盘 | 至少预留 3 GB（依赖环境约 1.2 GB；勾选可选组件则需 5 GB+） |

> 若未安装 Python：到 <https://www.python.org/downloads/windows/> 下载安装，
> 安装时**务必勾选 “Add python.exe to PATH”**。

## 二、安装（三步）

1. **解压**：把整个压缩包解压到一个**没有中文和空格**的目录，例如 `D:\\Zhishiku`。
   （不要直接在压缩包内运行脚本。）
2. **双击**：进入解压后的文件夹，双击「**一键安装.cmd**」。
3. **等待**：脚本会自动检测 Python → 创建虚拟环境 → 安装依赖 → 建库初始化，
   全程约 3 ~ 10 分钟。看到 `INSTALLATION COMPLETE` 即安装成功。

安装完成后，双击「**启动平台.cmd**」启动，浏览器会自动打开
<http://127.0.0.1:8000/>。

## 三、安装脚本做了什么

| 步骤 | 动作 | 说明 |
|---|---|---|
| 1 | 查找 Python | 按 `3.13 → 3.12 → 3.11 → 3.10 → 任意版本 → python → 常见安装路径` 的顺序探测。每个候选都会校验「**版本 ≥ 3.10 且自带 venv 模块**」，不可用就自动跳到下一个；可用环境变量 `ZHISHIKU_PYTHON` 直接指定 |
| 2 | 创建虚拟环境 | 建立在 `XiTong\\venv`，与系统 Python 隔离，不污染全局 |
| 3 | 安装依赖 | 按 `XiTong\\requirements.txt` 安装；先用清华镜像，失败自动回退官方源 |
| 4 | 准备数据库 | 执行 `migrate` 建表；若包内已带 `db.sqlite3`（迁移包）则原样保留 |
| 5 | 初始化 | 写入默认分库（运行档案库 / 知识库）与默认设置 |

### 可选组件（视频转图文 / OCR / 语音转写）

默认**不装**（含 PyTorch，体积数 GB）。需要时执行：

```
install\\install.cmd --full
```

或用命令行安装：`XiTong\\venv\\Scripts\\pip install -r XiTong\\requirements-optional.txt`

> OCR 还需要另外安装 Tesseract-OCR 并加入 PATH。

## 四、包内结构

```
{pkg}/
├─ XiTong/            平台程序（源码、模板、静态资源、依赖清单、启动脚本）
│   ├─ manage.py  kb/  core/  11 个功能模块 …
│   ├─ bin/ffmpeg.exe 视频转图文所需的外部工具
│   └─ media/         上传媒体目录
├─ ZhiShi/            知识库目录（与 XiTong 同级，平台自动定位）
├─ install/
│   ├─ install.cmd      一键安装脚本
│   ├─ start-platform.cmd 启动脚本
│   └─ stats.py           安装后统计
├─ 一键安装.cmd         ← 双击这个
├─ 启动平台.cmd         ← 装完双击这个
├─ 备份清单.json        本次备份的范围与内容清单
├─ 备份说明.md          本次备份的范围说明
└─ VERSION.txt
```

## 五、常见问题

**Q：双击后窗口一闪而过？**
在文件夹空白处右键 →「在终端中打开」，手动执行 `install\\install.cmd`，
就能看到完整报错信息。

**Q：提示找不到 manage.py？**
没有解压完整，或仍在压缩包内运行。请先完整解压到本地目录再执行。

**Q：提示找不到可用的 Python？**
两种可能：① 本机没装 Python；② 装的是**精简版或被搬迁过的** Python，缺少 `venv` 模块
（症状：`python -V` 一切正常，但 `python -m venv xxx` 报 `No module named venv`）。
到 python.org 装一个**完整版**并勾选 “Add python.exe to PATH” 即可。机器上有多个 Python 时，
可用 `set ZHISHIKU_PYTHON=C:\\path\\to\\python.exe` 精确指定。

**Q：依赖安装失败？**
多为网络问题。设置环境变量 `ZHISHIKU_NO_MIRROR=1` 后重试（改用官方源），
或先配置好可用的 pip 镜像。

**Q：端口 8000 被占用？**
启动脚本会自动释放旧的监听进程；若仍冲突，编辑 `XiTong\\start-zhishiku.cmd`
中的 `PORT` 值即可。

**Q：想换一个知识数据目录？**
设置环境变量 `ZHISHIKU_DATA_ROOT` 指向目标目录，例如
`set ZHISHIKU_DATA_ROOT=D:\\MyZhishi`，再启动平台。
不设置时默认使用平台程序同级的 `ZhiShi` 目录。

**Q：解压后中文文件名乱码？**
Windows 自带解压对 UTF-8 中文名支持不佳，请改用 **7-Zip** 或 **WinRAR** 解压。

## 六、备份包的三种类型（对照）

| 备份类型 | 程序代码 | 依赖环境 | 知识资料 | 数据库 | 用途 |
|---|---|---|---|---|---|
| 知识备份 | ✗ | ✗ | ✅ 全部 | 导出为 JSON | 只留知识，随时找回 |
| 系统备份 | ✅ 全部 | ✅ 脚本重建 | ✗ 零知识 | 新建空库 | 发给他人装空平台 |
| 迁移备份 | ✅ 全部 | ✅ 脚本重建 | ✅ 全部 | ✅ 原文件 | 换电脑整体搬迁 |
'''


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
        'Runtime need  : Python 3.10+  (see install/install.cmd)',
        '',
        'Entry points:',
        '  install/install.cmd          one-click installer',
        '  install/start-platform.cmd   start the platform',
        '',
        'See the Chinese guide (.md in the package root) for details.',
    ]
    return _crlf('\n'.join(lines) + '\n')
