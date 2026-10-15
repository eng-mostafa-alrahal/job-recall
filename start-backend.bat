@echo off
title JobRecall Backend Service
cd /d "%~dp0"

echo ========================================================
echo   JobRecall Local Backend Service
echo   Running at http://127.0.0.1:8765
echo ========================================================
echo.

if not exist "backend\.venv" (
    echo [ERROR] Virtual environment not found. Run npm run build and setup first.
    pause
    exit /b 1
)

.\backend\.venv\Scripts\uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
pause
