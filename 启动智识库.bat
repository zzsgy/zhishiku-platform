@echo off
rem Kept for backward compatibility: desktop icons / shortcuts that point at
rem this CJK-named file must keep working. All real logic lives in
rem start-zhishiku.cmd (ASCII name, cwd-safe, single source of truth).
call "%~dp0start-zhishiku.cmd"
exit /b %errorlevel%
