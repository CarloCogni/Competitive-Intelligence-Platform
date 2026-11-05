@echo off
setlocal
title Competitive Intelligence Dashboard
cd /d "%~dp0"

echo ============================================
echo  Competitive Intelligence Dashboard
echo ============================================
echo.
echo  URL:      http://localhost:8000
echo  Login:    batman / the hero we deserve
echo.
echo  Press Ctrl+C to stop the server.
echo.

echo Installing dependencies...
uv sync

echo.
echo Applying migrations...
uv run python src/manage.py migrate

echo.
timeout /t 2 /nobreak >nul
start "" http://localhost:8000

uv run python src/manage.py runserver
pause
