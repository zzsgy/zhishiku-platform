@echo off
setlocal
title ZhiShiKu Platform - Stop
echo ============================================
echo   ZhiShiKu Platform - Stop Service
echo ============================================
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if($c){ $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Host ('[OK] Stopping server process PID ' + $_); Stop-Process -Id $_ -Force } } else { Write-Host '[INFO] Service is not running.' }"
echo Done.
ping -n 4 127.0.0.1 >nul
exit /b 0
