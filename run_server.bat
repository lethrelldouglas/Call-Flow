@echo off
title Voice server (leave this window open)
cd /d "%~dp0"
python voice_server.py
echo.
echo The server stopped. Press any key to close this window.
pause >nul
