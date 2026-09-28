@echo off
title Tunnel (leave this window open)
cd /d "%~dp0"
set CF=cloudflared
if exist "C:\Program Files (x86)\cloudflared\cloudflared.exe" set CF=C:\Program Files (x86)\cloudflared\cloudflared.exe
if exist "C:\Program Files\cloudflared\cloudflared.exe" set CF=C:\Program Files\cloudflared\cloudflared.exe
if exist tunnel.log del tunnel.log
"%CF%" tunnel --url http://localhost:8000 --no-autoupdate --logfile tunnel.log
echo.
echo The tunnel stopped. Press any key to close this window.
pause >nul
