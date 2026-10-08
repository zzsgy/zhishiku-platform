@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-platform.ps1" -Action Start
exit /b %errorlevel%
