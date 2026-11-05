#!/bin/sh
set -e
cd "$(dirname "$0")"

echo "============================================"
echo " Competitive Intelligence Dashboard"
echo "============================================"
echo ""
echo " URL:      http://localhost:8000"
echo " Login:    batman / the hero we deserve"
echo ""
echo " Press Ctrl+C to stop the server."
echo ""

echo "Installing dependencies..."
uv sync

echo ""
echo "Applying migrations..."
uv run python src/manage.py migrate

echo ""
echo "Starting server..."
uv run python src/manage.py runserver
