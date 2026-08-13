# Agri-Vehicle Rental — Backend API

Backend for a mobile app that connects renters with verified local
agricultural-vehicle owners and drivers in Tier 2 cities.

**Stack:** Python 3.11+ · FastAPI · PostgreSQL + PostGIS · SQLAlchemy 2.0 (async) · Alembic

| Document | What's in it |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | Install and run it on your machine, step by step |
| [`docs/PROJECT.md`](docs/PROJECT.md) | Requirements, architecture decisions, schema, roadmap, open questions |
| [`mvp/`](mvp/) | The original MVP scope PDF |

---

## Quick start

```powershell
uv sync --extra dev                                  # install dependencies
Copy-Item .env.example .env                          # create your local config
uv run uvicorn app.main:app --reload --port 8000     # run it
```

Then open <http://localhost:8000/docs> — interactive API documentation,
generated from the code.

> Data endpoints need PostgreSQL running. Until then the app still starts and
> `/health` works; `/ready` will report the database as unavailable. See
> `docs/SETUP.md` §1 to install PostgreSQL + PostGIS.

## Everyday commands

```powershell
uv run uvicorn app.main:app --reload --port 8000     # run the API
uv run pytest                                        # tests
uv run ruff format . ; uv run ruff check . --fix     # format + lint
uv run mypy app                                      # type check
uv run alembic upgrade head                          # apply DB migrations
uv run alembic revision --autogenerate -m "message"  # create a migration
```

## Layout

```
app/
├── main.py            # create_app() — builds and wires the application
├── core/              # config, logging, error handling, middleware
├── db/                # engine, session, base models, migrations
├── modules/           # one folder per feature (auth, providers, vehicles, ...)
├── integrations/      # adapters for outside services (SMS, storage)
└── api/
    ├── health.py      # /health, /ready
    └── v1/router.py   # mounts feature routers under /api/v1
tests/                 # pytest suite
docs/                  # project tracker + setup guide
```

**The layering rule:** `router` → `service` → `repository`.
Routers do HTTP only. Services hold business logic. Repositories own SQL.
ORM models (`models.py`) and API schemas (`schemas.py`) are always separate —
that separation is what stops private data leaking into a response.

## Endpoints today

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service banner |
| GET | `/health` | Liveness — is the process up? |
| GET | `/ready` | Readiness — are dependencies reachable? |
| GET | `/docs` | Interactive API docs (disabled in production) |

Feature endpoints arrive with Phase 1 (authentication). See
[`docs/PROJECT.md`](docs/PROJECT.md) §11 for the roadmap.

## Environments

Two: **`local`** (development and testing, local database) and
**`production`** (decided later). Configuration comes entirely from
environment variables — `.env` locally, injected variables in production.

**Secrets are never committed.** `.env` is git-ignored; only `.env.example`
is tracked, and it holds placeholders.
