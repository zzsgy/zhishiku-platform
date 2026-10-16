@echo off
rem Zhishiku LAN launcher: binds 0.0.0.0 and allows Host 192.168.20.222
rem Other computers on the LAN can visit http://192.168.20.222:8613/
cd /d %~dp0
set ZHISHIKU_ALLOWED_HOSTS=127.0.0.1,localhost,[::1],192.168.20.222
start "zhishiku-worker" /min "%~dp0venv\Scripts\python.exe" -X utf8 manage.py worker
"%~dp0venv\Scripts\python.exe" -X utf8 manage.py serve --port 8613 --host 0.0.0.0
pause
