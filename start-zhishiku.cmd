@echo off
setlocal EnableExtensions
rem ============================================================
rem  ZhiShiKu platform launcher   (Django, http://127.0.0.1:8000)
rem
rem  Why this file exists:
rem   * ASCII-only + CRLF on purpose. cmd.exe mis-parses LF-only or
rem     non-ASCII batch files (the old CJK-named launcher was fragile).
rem   * Every path is derived from %~dp0 and we cd there first, so the
rem     launcher behaves identically whether it is double-clicked on the
rem     desktop, run from the Startup folder, or called from another cwd.
rem     The old version ran a bare relative "manage.py" and died with
rem     "can't open file ...\manage.py" whenever the cwd was not the project.
rem   * Django runs in the FOREGROUND of this console: the window IS the
rem     service. No PID scraping (the old PID probe was malformed by cmd.exe
rem     quoting and always reported a false failure) and nothing is hidden,
rem     which this machine's security software (Huorong) is known to kill.
rem   * ping is used for short waits: "timeout" aborts with
rem     "input redirection is not supported" when stdin is not a console.
rem ============================================================
cd /d "%~dp0"
title ZhiShiKu Platform (port 8000)

set "BASE=%~dp0"
set "PY=%BASE%venv\Scripts\python.exe"
set "MANAGE=%BASE%manage.py"
set "WAIT=%BASE%zhishiku-wait.ps1"
set "URL=http://127.0.0.1:8000/"
set "HOSTADDR=127.0.0.1"
set "PORT=8000"

echo ============================================
echo   ZhiShiKu Platform - Launcher
echo   URL : %URL%
echo   CWD : %CD%
echo ============================================

if not exist "%PY%" (
  echo [ERROR] python interpreter not found:
  echo         %PY%
  echo.
  pause
  exit /b 1
)
if not exist "%MANAGE%" (
  echo [ERROR] manage.py not found:
  echo         %MANAGE%
  echo.
  pause
  exit /b 1
)
if not exist "%WAIT%" (
  echo [ERROR] readiness helper not found:
  echo         %WAIT%
  echo.
  pause
  exit /b 1
)

rem ---- 1. service already answering? just show it ----
rem      UP is only set when the probe really ran and really succeeded,
rem      so a missing/broken probe can never fake an "already running" state.
set "UP="
powershell -NoProfile -ExecutionPolicy Bypass -File "%WAIT%" probe "%URL%" >nul 2>&1
if not errorlevel 1 set "UP=1"
if defined UP (
  echo [OK] Service is already running - opening %URL%
  start "" "%URL%"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

rem ---- 2. clear a stale/zombie listener so the port is really free ----
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
  echo [..] Releasing stale listener on port %PORT% ^(pid %%p^)
  taskkill /PID %%p /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

rem ---- 3. open the browser as soon as the service answers ----
rem      start /b reuses THIS console, so no extra window flashes
start /b "" powershell -NoProfile -ExecutionPolicy Bypass -File "%WAIT%" open "%URL%"

echo [..] Starting Django on %HOSTADDR%:%PORT% ...
echo      Keep this window open while you use the platform.
echo      Close it (or press Ctrl+C) to stop the service.
echo.

"%PY%" "%MANAGE%" runserver %HOSTADDR%:%PORT% --noreload
set "RC=%errorlevel%"

echo.
echo [INFO] Server stopped (exit code %RC%).
echo        %URL% is no longer served.
pause
exit /b %RC%
