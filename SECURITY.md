# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report vulnerabilities privately through GitHub:
**Security → Report a vulnerability** on this repository
([direct link](https://github.com/CarloCogni/Competitive-Intelligence-Platform/security/advisories/new)).

Include steps to reproduce and the affected version or commit. You can expect an initial response within a week.

## Supported versions

Only the latest release on `master` receives fixes.

## Deployment notes

- The demo account created by `seed_demo` (`batman`) has a publicly known password. Never run `seed_demo` or use the demo database on an internet-facing deployment.
- Use `config.settings.production` in production and set `SECRET_KEY`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` via environment variables.
- API keys (GitHub, Groq, Twitter) belong in `.env` or the admin UI — never in the repository.
