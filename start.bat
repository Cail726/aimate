@echo off
chcp 65001 >nul
title AIMate

cd /d "%~dp0"

:: Check Python
python --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

:: Kill old instance
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7799.*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>nul
)

:: Install deps
pip install fastapi uvicorn pyyaml httpx jinja2 flask waitress requests -q 2>nul

:: Launch
echo.
echo   AIMate is starting...
echo   URL: http://127.0.0.1:7799
echo.
start "" http://127.0.0.1:7799
python server.py

pause

