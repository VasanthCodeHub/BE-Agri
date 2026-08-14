# Setup Guide

Getting the backend running on **Windows 11**, step by step, with an
explanation of why each piece exists.

**Status:** Phase 0 (the foundation) is built and passing. The app runs today.
The remaining step is installing PostgreSQL — §1.

---

## 0. The picture

```
   Flutter app (phone)
          │  HTTPS / JSON
          ▼
  ┌───────────────────┐
  │  FastAPI (Python) │   your code
  └─────────┬─────────┘
            └──────────▶ PostgreSQL + PostGIS    all data + radius search
```

That's the whole stack for now. Later additions (an SMS gateway for real OTPs,
object storage for photos, Redis for shared rate limits) each get added when
they're actually needed — right now the SMS provider is a **fake** that prints
the OTP to your terminal, and uploads go to a local folder.

| Tool | What it is | Why we need it |
|---|---|---|
| **Python 3.11+** | Language runtime | FastAPI runs on it |
| **uv** | Package + virtualenv manager | Installs dependencies; the lockfile makes every machine identical |
| **PostgreSQL 16** | The database | Stores users, providers, vehicles, documents |
| **PostGIS** | PostgreSQL extension | "Find providers within 25 km" — fast and accurate |
| **FastAPI** | Web framework | Routing, validation, auto-generated docs |
| **Uvicorn** | ASGI server | Listens on the port and runs FastAPI |
| **Pydantic v2** | Validation | Turns untrusted JSON into typed Python objects |
| **SQLAlchemy 2.0** | ORM | Python classes ↔ database tables, safely |
| **Alembic** | Migrations | Version control for your database schema |
| **pytest** | Tests | Proves it works |
| **ruff / mypy** | Lint / types | Catches mistakes before runtime |

### The one FastAPI concept to internalise

**Dependency injection.** You declare what a route *needs* as a parameter, and
FastAPI provides it:

```python
@router.get("/me")
async def get_me(
    current_user: User = Depends(get_current_user),   # authentication
    db: AsyncSession = Depends(get_db),               # database session
) -> UserRead:                                        # response shape
    return await user_service.get_profile(db, current_user.id)
```

Three things happen with no code in the function body: the request is
authenticated, a database session is opened and closed (even on error), and the
response is validated against `UserRead`. That last one is why private fields
can't leak — the return type decides what goes out.

---

## 1. PostgreSQL

### 1.1 Installed version

**PostgreSQL 18** is installed and the `postgresql-x64-18` service is running.
Installation path: `C:\Program Files\PostgreSQL\18`.

If you ever reinstall: the Windows installer is at
<https://www.postgresql.org/download/windows/> (the EDB installer). Keep the
port as **5432**, and note the `postgres` superuser password — there is no
recovery if you lose it.

### 1.2 PostGIS — needed from Phase 6, not before

> **Heads-up:** EDB **removed Stack Builder** from the PostgreSQL installer in
> recent versions, so on PG18 there is no "Spatial Extensions" step to tick.
> PostGIS is a separate download.

**PostGIS is not required yet.** Authentication, profiles, listings and
verification need no geo features. It becomes genuinely required at **Phase 6
(radius search)**, so you can install it any time before then.

The first migration checks whether PostGIS is available and skips it with a
warning if not, so `alembic upgrade head` works either way. `/ready` reports the
status, so you always know where you stand:

```json
{"ready": true, "checks": {"database": "ok", "postgis": "not_installed"}}
```

**When you're ready to install it:**

1. Go to <https://download.osgeo.org/postgis/windows/pg18/>
2. Download the newest `postgis-bundle-pg18x64-setup-3.6.x-1.exe`
   (PostGIS Bundle 3.6.2 supports PostgreSQL 14–18).
3. Run it and point it at `C:\Program Files\PostgreSQL\18`.
4. Then enable it in the database:
   ```powershell
   psql -U agri -d agri_local -c "CREATE EXTENSION postgis;"
   psql -U agri -d agri_test  -c "CREATE EXTENSION postgis;"
   ```

### 1.3 Put `psql` on your PATH

`psql` is PostgreSQL's command-line client. Adding it to PATH means you can
type `psql` instead of the full path:

```powershell
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
[Environment]::SetEnvironmentVariable(
    "Path",
    [Environment]::GetEnvironmentVariable("Path", "User") + ";$pgBin",
    "User"
)
```

**Close and reopen your terminal**, then check:

```powershell
psql --version
```

### 1.4 Create the database and user

Connect as the superuser (it will prompt for the password from step 1.1):

```powershell
psql -U postgres
```

At the `postgres=#` prompt, paste these four lines:

```sql
CREATE USER agri WITH PASSWORD 'agri_local_password' SUPERUSER;
CREATE DATABASE agri_local OWNER agri;
CREATE DATABASE agri_test  OWNER agri;
\q
```

What each line does:

- **`CREATE USER agri`** — a dedicated account for this app, rather than using
  the all-powerful `postgres` superuser for everyday work.
- **`SUPERUSER`** — needed so migrations can run `CREATE EXTENSION postgis`,
  which is a privileged operation. This is fine on your laptop. **In production
  the app user gets only data permissions**, and extensions/migrations are
  applied by a separate admin role.
- **`agri_local`** — your development database.
- **`agri_test`** — a separate database for the test suite, so running tests
  can never touch your development data.

The password here matches `DATABASE_URL` in `.env`. It's a local-only password,
which is why it can live in a template.

### 1.5 Verify the connection

```powershell
psql -U agri -d agri_local -c "SELECT current_user, current_database(), version();"
```

If that prints your user, database and the PostgreSQL version, the app can
connect too. To check whether PostGIS is present (optional until Phase 6):

```powershell
psql -U agri -d agri_local -c "SELECT name FROM pg_available_extensions WHERE name='postgis';"
```

An empty result means PostGIS isn't installed yet — see §1.2 when you need it.

---

## 2. Project setup

### 2.1 Install dependencies

```powershell
uv sync --extra dev
```

Reads `pyproject.toml` + `uv.lock`, creates `.venv/`, installs everything.
`--extra dev` includes the test and lint tools.

> **Why `uv run` everywhere?** It runs a command inside the project's virtual
> environment automatically, so you never have to activate anything, and you
> can't accidentally install into your system Python.

### 2.2 Your `.env` file

Already created. If you ever need a fresh one:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET_KEY
```

Remember: **`.env` is yours and git-ignored. `.env.example` is the committed
template with placeholders.** Same variable list, different values.

### 2.3 Apply migrations

```powershell
uv run alembic upgrade head
```

**What happens:** Alembic checks the `alembic_version` table in your database
to see which migrations have already run, then applies the missing ones in
order. Right now that's one migration, which enables PostGIS.

The commands you'll use constantly:

| Command | Purpose |
|---|---|
| `alembic upgrade head` | Apply all pending migrations |
| `alembic revision --autogenerate -m "add users"` | Diff models vs database, write a migration |
| `alembic downgrade -1` | Undo the last migration |
| `alembic current` | Which migration is this database on? |
| `alembic history` | List all migrations |
| `alembic upgrade head --sql` | Print the SQL instead of running it |

> ⚠️ **Always read an autogenerated migration before applying it.**
> Autogenerate is a helpful draft, not an oracle. It cannot see a *rename* — it
> writes a `drop_column` + `add_column`, which **destroys the data** in that
> column. Reviewing migrations is a habit worth building from your first one.

### 2.4 Run the API

```powershell
uv run uvicorn app.main:app --reload --port 8000
```

`--reload` restarts on every file save. **Development only** — never in production.

| URL | What it is |
|---|---|
| <http://localhost:8000/docs> | **Swagger UI** — interactive docs. Click "Try it out" to call endpoints. |
| <http://localhost:8000/redoc> | Cleaner read-only reference |
| <http://localhost:8000/openapi.json> | The raw API spec — **give this to the Flutter developer**; they can generate a client from it |
| <http://localhost:8000/health> | Liveness — is the process up? |
| <http://localhost:8000/ready> | Readiness — is the database reachable? |

> `/docs` is FastAPI's best feature and it's free. It's generated from your type
> hints and Pydantic schemas — so **writing accurate types directly improves
> your API documentation.**

### 2.5 SMS delivery — where the Twilio credentials go

**You do not need Twilio to develop.** Locally `SMS_PROVIDER=fake` prints the
code in your terminal, and `OTP_DEV_BYPASS_CODE=0000` logs in any number. Twilio
is only switched on in production.

#### The flow (we own the OTP, Twilio just carries it)

```
user enters phone → our backend generates a 4-digit code and stores its
Argon2 hash → Twilio Messaging API sends the SMS → user types the code →
our backend verifies it against the stored hash
```

This is Twilio **Programmable SMS**. It is *not* Twilio Verify, so there is no
`TWILIO_VERIFY_SERVICE_SID` in this project — with Verify, Twilio would generate,
store and check the code, replacing the `otp_requests` table, the expiry, the
attempt limit and the role-locked-to-the-code rule we already have.

#### Where to put the values

All three go in **`.env`**, under `# --- Twilio (production only) ---`:

| Variable | Where to find it in the Twilio console |
|---|---|
| `TWILIO_ACCOUNT_SID` | Console **dashboard**, "Account Info" panel — starts with `AC` |
| `TWILIO_AUTH_TOKEN` | Same panel, click **Show** to reveal it |
| `TWILIO_PHONE_NUMBER` | **Phone Numbers → Manage → Active numbers**, in E.164 (`+12025550123`) |

```dotenv
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+12025550123
```

> ⚠️ The auth token is a **password to your Twilio account** — anyone holding it
> can send SMS you pay for. `.env` is git-ignored, so it never leaves your
> machine. Production does **not** use a `.env` file at all: the same three
> variables are injected as real environment variables by the host, and the
> `Settings` class reads them identically.

#### Sending a real SMS from your machine (optional, costs money)

```dotenv
SMS_PROVIDER=twilio       # was: fake
```

Restart the server. Nothing else changes — the factory in
`app/integrations/sms/__init__.py` swaps the implementation and the auth code is
untouched. Set it back to `fake` afterwards.

The app **refuses to start** if `SMS_PROVIDER=twilio` and any of the three values
is missing or malformed, rather than failing on a real user's login.

#### A trial account cannot send our OTP — it must be upgraded first

Tested against a real trial account. This is a hard blocker:

```
572006  Invalid template name. Trial accounts can only use predefined SMS templates.
```

On trial, `Body` must be one of about ten Twilio template *ids* (`sms_2fa`,
`sms_account_alerts`, …) and **Twilio substitutes its own text**. Sending
`Body=sms_2fa` is accepted, and the phone receives:

> Your verification code is **482913**. It expires in 5 minutes. Do not share
> it. Test message from Twilio.

`482913` is Twilio's fixed sample, not the code our backend generated — so login
can never complete. **There is no code-side workaround.** Stay on
`SMS_PROVIDER=fake` until the Twilio account is upgraded (add a payment method).

Other trial-only oddities, so they don't send you hunting:

- Every message gets `"Test message from Twilio."` appended.
- Only pre-verified destination numbers can receive SMS (`21608` otherwise).
- `Balance`, `OutgoingCallerIds` and `Alerts` return `401 "not available on a
  Trial account"`, and `IncomingPhoneNumbers` returns an **empty list** — so an
  empty number list does *not* mean your sender number is unusable. Check the
  Messages log instead; that one works.

#### After upgrading: India needs DLT registration

TRAI requires a registered entity, header (sender ID) and message template; an
unregistered sender gets filtered by the carrier (error `30007`), often
*silently*. The wording lives in `SMS_OTP_TEMPLATE` precisely so it can be made
to match the registered template character for character.

---

## 3. Daily workflow

```powershell
uv run alembic upgrade head                          # 1. schema up to date
uv run uvicorn app.main:app --reload --port 8000     # 2. code
```

Before committing:

```powershell
uv run ruff format .          # format
uv run ruff check . --fix     # lint + autofix
uv run mypy app               # type check
uv run pytest                 # tests
```

Once you `git init`, install the hooks and the first three run automatically on
every commit:

```powershell
uv run pre-commit install
```

---

## 4. Testing

```powershell
uv run pytest                              # everything
uv run pytest -k "health"                  # matching tests only
uv run pytest -x -vv                       # stop at first failure, verbose
uv run pytest --cov=app --cov-report=html  # coverage → htmlcov/index.html
```

**How the setup works:**

- `tests/conftest.py` holds shared fixtures. pytest imports it automatically —
  no import needed in test files.
- The `client` fixture calls the app **in-process** via httpx's ASGI transport.
  Real routing, middleware, validation and serialisation — but no network and
  no server to start. Fast and realistic.
- Tests use `agri_test`, never your development database.
- The SMS provider is the fake one, so tests never send a message.

The tests that will matter most as we build:

1. **Ownership** — Provider A gets `404` touching Provider B's vehicle.
2. **Privacy** — no renter-facing response ever contains a phone number.
3. **Verification** — illegal status transitions are rejected.
4. **Search** — a provider 30 km away is excluded from a 25 km radius.
5. **Rate limiting** — the 4th OTP request in an hour is refused.

---

## 5. Windows notes

### 5.1 This folder is inside OneDrive

`C:\Users\Admin\OneDrive\Desktop\agri` is a **synced** folder, which causes real
problems for code:

- OneDrive syncs `.venv/` and `__pycache__/` — thousands of churning files,
  wasted bandwidth, and occasional file locks that break installs mid-run.
- Sync conflicts can create `main (2).py`-style duplicates in your source tree.
- **Biggest risk:** your `.env`, which now holds a real signing key, gets
  uploaded to cloud storage.

**Recommended:** move the project to `C:\dev\agri`. If it stays here, exclude
`.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache` and `.ruff_cache` from
OneDrive sync.

### 5.2 UTF-8

Windows terminals default to the `cp1252` charset, so printing a character like
`→` raises `UnicodeEncodeError`. Set this permanently in your system
environment variables:

```
PYTHONUTF8=1
```

And in code, always be explicit: `open(path, "w", encoding="utf-8")`.
Our `Settings` already forces UTF-8 when reading `.env`.

### 5.3 PowerShell is not bash

In Windows PowerShell 5.1, `&&` is a **syntax error**:

```powershell
command-a; if ($?) { command-b }        # run b only if a succeeded
command-a; command-b                    # run both regardless
$env:APP_ENV = "local"; uv run pytest   # set an env var (no bash-style prefix)
```

### 5.4 Port already in use

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <pid>
```

### 5.5 When you're ready for git

```powershell
git init
git branch -M main
git config core.autocrlf input     # stops line-ending noise in diffs
uv run pre-commit install
```

`.gitignore` is already in place, so your `.env` is protected from the first
commit onward. **Never commit a `.env`** — once a secret is in git history,
rewriting history is the only fix, and if it was pushed anywhere the secret
must be treated as compromised.

---

## 6. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `ModuleNotFoundError` | Wrong interpreter — use `uv run <command>` |
| `connection refused` on 5432 | PostgreSQL service isn't running. Check `services.msc` → postgresql-x64-18 |
| `password authentication failed for user "agri"` | Password mismatch between §1.4 and `DATABASE_URL` in `.env` |
| `database "agri_local" does not exist` | Re-run the `CREATE DATABASE` step in §1.4 |
| `type "geography" does not exist` | PostGIS not enabled — run §1.5, then `alembic upgrade head` |
| `could not open extension control file` | PostGIS not installed — redo §1.2 via Stack Builder |
| `DATABASE_URL must use the async driver` | You wrote `postgresql://`; it needs `postgresql+asyncpg://` |
| `Target database is not up to date` | Run `alembic upgrade head` |
| Autogenerate produces an empty migration | Your model isn't imported in `app/db/models.py` |
| API hangs or gets slow | A **blocking call inside `async def`** — the classic FastAPI mistake. Wrap sync libraries in `run_in_threadpool`. |
| `MissingGreenlet` | Touching a lazy-loaded attribute outside a session. Use `selectinload` to load relations up front. |
| 422 response | Pydantic validation failed. The body names the exact field and rule. |

---

## 7. Learning path

In the order you'll meet them in this project:

1. **Path/query params + Pydantic models** → profile endpoints
2. **`Depends()`** → `get_db`, `get_current_user` (Phase 1, next)
3. **Response models** → how private fields are kept out of responses
4. **`async`/`await`** → why one blocking call hurts every request
5. **SQLAlchemy 2.0 async sessions + relationships** → every module
6. **Alembic migrations** → from Phase 1 onward
7. **Middleware & exception handlers** → already built in `app/core/`
8. **Testing with `httpx.AsyncClient`** → every phase

Official tutorial: <https://fastapi.tiangolo.com/tutorial/> — genuinely
excellent, worth reading top to bottom.

**The habit that matters most:** when the `router → service → repository`
split feels like extra typing, that's the rule earning its value. It's what
lets you test business logic without HTTP, change queries without touching
routes, and hand a module to another developer without explaining the whole app.

---

## Cheat sheet

```powershell
# Setup
uv sync --extra dev
Copy-Item .env.example .env

# Database
psql -U agri -d agri_local                          # open a SQL prompt
uv run alembic upgrade head                         # apply migrations
uv run alembic revision --autogenerate -m "message" # create one
uv run alembic current                              # where am I?

# Run
uv run uvicorn app.main:app --reload --port 8000

# Quality
uv run ruff format . ; uv run ruff check . --fix
uv run mypy app
uv run pytest

# Utilities
python -c "import secrets; print(secrets.token_urlsafe(48))"
```
