#!/bin/sh
set -e
cd "$(dirname "$0")"

echo "============================================"
echo " CompIntel - Competitive Intelligence Dashboard"
echo "============================================"
echo ""

FRESH_DB=0
[ -f src/db.sqlite3 ] || FRESH_DB=1

echo "Installing dependencies..."
uv sync

echo ""
echo "Applying migrations..."
uv run python src/manage.py migrate

if [ "$FRESH_DB" = 1 ]; then
    echo ""
    echo "New database: loading entities and demo data..."
    uv run python src/manage.py seed
    uv run python src/manage.py seed_demo
    echo ""
    echo " Tip: for real collected data (metrics, news, alerts), download db.sqlite3.zip from"
    echo " https://github.com/CarloCogni/Competitive-Intelligence-Platform/releases/latest"
    echo " and unzip it into src/ (replace the new database)."
fi

echo ""
echo " URL:      http://localhost:8000"
echo " Login:    batman / the hero we deserve   (local demo only)"
echo ""
echo " Press Ctrl+C to stop the server."
echo ""
uv run python src/manage.py runserver
