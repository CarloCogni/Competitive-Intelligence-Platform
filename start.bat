@echo off
setlocal
title CompIntel
cd /d "%~dp0"

echo ============================================
echo  CompIntel - Competitive Intelligence Dashboard
echo ============================================
echo.

set FRESH_DB=0
if not exist src\db.sqlite3 set FRESH_DB=1

echo Installing dependencies...
uv sync || goto :error

echo.
echo Applying migrations...
uv run python src/manage.py migrate || goto :error

if "%FRESH_DB%"=="1" (
    echo.
    echo New database: loading entities and demo data...
    uv run python src/manage.py seed || goto :error
    uv run python src/manage.py seed_demo || goto :error
    echo.
    echo  Tip: for months of real history, download db.sqlite3.zip from
    echo  https://github.com/CarloCogni/Competitive-Intelligence-Platform/releases/latest
    echo  and unzip it into src\ ^(replace the new database^).
)

echo.
echo  URL:      http://localhost:8000
echo  Login:    batman / the hero we deserve   ^(local demo only^)
echo.
echo  Press Ctrl+C to stop the server.
echo.
timeout /t 2 /nobreak >nul
start "" http://localhost:8000

uv run python src/manage.py runserver
pause
exit /b 0

:error
echo.
echo Setup failed - see the error above.
pause
exit /b 1
