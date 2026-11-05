# Setup Guide

---

## Local development (Python 3.13+ required)

**Prerequisites:** Python 3.13+, [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
git clone https://github.com/CarloCogni/Competitive-Intelligence-Platform.git
cd Competitive-Intelligence-Platform

# Install dependencies
uv sync

# Configure environment
cp .env.example .env

# Set up and seed the database
uv run python src/manage.py migrate
uv run python src/manage.py seed
uv run python src/manage.py seed_demo   # admin user + demo data

# Start the server
uv run python src/manage.py runserver
```

Open **http://localhost:8000**

**Run the test suite:**
```bash
uv run pytest src/ -v
```

All external API calls are mocked — no network access needed for tests.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 8000 already in use | Run `uv run python src/manage.py runserver 8001` to use port 8001 instead |
| Dashboard loads but no data | Run `uv run python src/manage.py fetch` to trigger a manual data fetch |
| GitHub fetcher returns 403 | Add `GITHUB_TOKEN` to your `.env` — you've hit the unauthenticated rate limit |
| `seed` command is not found | Make sure you're running from the project root and that `uv sync` completed without errors |
| Dashboards look empty / no history | Download `db.sqlite3.zip` from the [latest Release](https://github.com/CarloCogni/Competitive-Intelligence-Platform/releases/latest) and unzip it into `src/` |
