# Agri-Vehicle Rental — Backend Project Tracker

> **Living document.** Requirements, architecture decisions, open questions and
> progress. Updated as we build.

| Field | Value |
|---|---|
| Project | Agri-Vehicle Rental App — Backend API |
| Scope source | `mvp/Agri_Vehicle_Rental_MVP.pdf` |
| Stack | Python 3.11+ · FastAPI · PostgreSQL 16 + PostGIS · SQLAlchemy 2.0 async · Alembic |
| Mobile client | Flutter (separate team) |
| Environments | `local` · `production` |
| Database | PostgreSQL **18** running locally, `agri_local`, migration `0001` applied ✅ |
| Status | **Phase 0 complete** — foundation built and verified. Next: Phase 1 (auth) |
| Last updated | 2026-08-13 |

---

## 1. Requirements

### 1.1 Product

A mobile app connecting renters who need agricultural vehicles with verified
local vehicle owners/drivers. Launch targets **Tier 2 cities**. One app, two
login experiences (User and Provider), plus admin operations.

### 1.2 Roles

| Role | MVP capabilities |
|---|---|
| **User / Renter** | Register/login, basic profile, search nearby, view listing, initiate protected call |
| **Provider / Owner / Driver** | Register/login, provider profile, add vehicle, upload docs/photos, manage listing status |
| **Admin** | Manually onboard providers, review documents, approve/reject, support ops |

### 1.3 MVP scope (PDF §3)

| ID | Area | Requirement | Backend implication |
|---|---|---|---|
| R1 | Authentication | User & Provider login/register, role routing | OTP auth, JWT |
| R2 | User profile | Basic renter profile | `user_profiles` |
| R3 | Provider profile | Identity, contact, service info | `provider_profiles` + geo point |
| R4 | Vehicle listing | Details, photos, rental/service info | `vehicles` + `vehicle_photos` |
| R5 | Provider verification | Upload RC book/photos; admin approves | Documents + state machine + audit |
| R6 | Location search | Providers within a configurable radius | PostGIS `ST_DWithin` + GiST index |
| R7 | Listing details | Info before contacting | Detail endpoint, **no phone number** |
| R8 | Contact | App-routed calling, numbers not exposed | Telephony masking (vendor TBD) |
| R9 | Basic status | Verification/availability status | Only approved + available discoverable |
| R10 | Basic admin | Manual registration/verification | Admin endpoints + audit log |

### 1.4 Out of MVP scope (PDF §7)

Job marketplace · classifieds · real estate · advanced ratings/reviews ·
dispute management · analytics dashboards · multi-district scale-out ·
fleet/enterprise features.

### 1.5 Documented future roadmap (PDF §8)

Jobs · Classifieds · Real Estate · Tier 1 expansion · district expansion ·
advanced booking (date/time availability) · payments & settlement · ratings &
reviews · notifications (push/SMS/WhatsApp) · advanced admin & analytics.

These shape the architecture (we leave seams) but **no code is written for them**.

---

## 2. Architecture decisions

Numbered as ADRs so they can be referenced and revisited. Decisions that have
changed since the first draft are marked **Revised**.

### ADR-001 — Modular monolith, not microservices

One deployable FastAPI app, internally split into feature modules with strict
layering:

```
router.py      HTTP only: parse request, call service, shape response.
service.py     Business logic, authorisation rules, transactions.
repository.py  Data access only. Owns queries.
models.py      SQLAlchemy ORM models (database shape).
schemas.py     Pydantic models (API shape) — deliberately separate.
```

**Why:** the MVP is a geographic pilot. Microservices would add network failure
modes, distributed transactions and deployment complexity for no benefit at this
scale. Clean module boundaries allow later extraction if a module ever needs it.

**Consequence:** ORM models and API schemas are never the same class. This is
what structurally prevents private data leaking into responses (ADR-009).

### ADR-002 — PostgreSQL 16 + PostGIS

Provider location stored as `geography(Point, 4326)` with a GiST index.

**Why:** radius search is the core query of the product. PostGIS gives
`ST_DWithin(location, :point, :metres)` — spheroid-accurate, index-accelerated,
with distance computed and sorted in the same query.

**Rejected:** lat/lng floats + haversine in Python. Cannot use an index, forces
a full table scan, moves distance maths into the app. Fine at 50 providers,
fails at 50,000 — and rewriting search later is expensive.

**Local install:** native Windows PostgreSQL installer + PostGIS via Stack
Builder (no Docker). See `docs/SETUP.md` §1.

### ADR-003 — Phone number + OTP authentication

Primary auth is **phone (E.164) + 6-digit SMS OTP**. Passwords only for admins.

**Why:** target users are agricultural vehicle owners and renters in Tier 2
cities — the PDF explicitly notes low digital literacy. Email is often absent;
passwords are a support burden. Phone number is also the natural identity for a
product whose core action is a phone call.

**Consequence:** OTP delivery is an external dependency and a **cost/abuse
vector** — see ADR-010 and open question Q3 (DLT registration).

### ADR-004 — JWT access tokens + rotating refresh tokens

- Access token: JWT, 15 min, carries `sub`, `role`, `jti`.
- Refresh token: opaque random string, 30 days, **stored hashed in the DB**,
  single-use with rotation and family revocation on reuse detection.

**Why:** mobile clients need long sessions without long-lived credentials.
Server-side refresh storage is what makes revocation possible at all — a purely
stateless JWT setup cannot revoke a stolen token.

**Important:** the `role` claim is for routing/UX convenience only. **Every
authorisation decision is re-checked server-side, per object.**

### ADR-005 — Async all the way down

`async def` endpoints, SQLAlchemy 2.0 async ORM with `asyncpg`, `httpx` for
outbound calls.

**Why:** this workload is I/O-bound (database, files, SMS). Async lets one
worker serve many concurrent requests cheaply.

**The trap:** one blocking call inside an `async def` stalls the entire event
loop. Any sync library must be wrapped in `run_in_threadpool`. This is the most
common cause of a mysteriously slow FastAPI app.

### ADR-006 — Alembic migrations from commit one

All schema changes go through Alembic. `create_all()` is never used outside
tests.

**Why:** local and production databases must evolve identically and
reproducibly. It's also the only safe way to enable PostGIS and create GiST /
partial indexes.

**Deployment rule:** migrations run as an explicit, separate step **before** the
new app version starts — never on application startup, where multiple workers
would race each other.

**Implementation note:** `alembic.ini` deliberately omits `sqlalchemy.url`;
`env.py` reads it from `Settings`, so no credential is ever committed.

### ADR-007 — Object storage with presigned URLs *(deferred)*

Photos and documents will go to private object storage, with short-lived
presigned URLs for upload and download. The database stores only the object key.

**Why:** files never touch the app server, so uploads don't consume app
CPU/bandwidth and the app stays stateless.

**Status: deferred.** `STORAGE_BACKEND=local` writes to a folder on disk for
now, behind a storage interface. Object storage is a config swap when we choose
a provider.

### ADR-008 — Telephony masking behind a provider-agnostic port *(vendor TBD)*

Protected calling requires a third-party masked-calling vendor. It will sit
behind an internal interface:

```python
class TelephonyProvider(Protocol):
    async def create_masked_call(
        self, from_user: PhoneNumber, to_provider: PhoneNumber, ref: str
    ) -> CallSession: ...
```

**Why a port:** number masking cannot be built in-house (it needs carrier-level
virtual numbers), telephony pricing changes, and tests must never place a real
call.

**Status: vendor not selected** — see Q4. Nothing is built yet. Candidates:
Exotel (strongest India coverage), Twilio, Plivo, Knowlarity.

### ADR-009 — Provider phone numbers are never serialised to renters

No response schema reachable by a renter contains a provider's phone number.
Contact happens only via the masked-call endpoint. Enforced structurally: every
route declares an explicit `response_model`, and provider phone exists only on
the ORM model, never on a public read schema.

**Why:** this is the product's stated privacy promise (PDF §2.2, §5.2 step 12).
Relying on developers to remember to exclude a field will fail; relying on the
schema layer will not.

Additionally, `app/core/logging.py` masks phone numbers in **every log line**
(`+9198****3210`), so they cannot leak through logs either.

### ADR-010 — **Revised:** rate limiting and OTP state in PostgreSQL for now

**Original decision:** Redis from day one for OTP state, rate-limit counters
and the JWT denylist.

**Revised:** OTP records go in a PostgreSQL table (hashed, with an expiry
column); rate-limit counters are in-process. Both behind an interface so Redis
can be introduced without touching business logic.

**Why revised:** locally we run a single process, where in-memory counters are
correct and Redis is one more service to install. **This must change before
production**, where multiple app instances make in-process counters useless.
Tracked as a pending task in §11 Phase 8.

**What has not changed:** OTP endpoints are a direct financial attack surface.
**SMS pumping fraud** — looping OTP requests to drain an SMS budget — is a
routine attack on Indian consumer apps. Per-phone and per-IP limits are MVP
scope, not hardening-phase scope.

### ADR-011 — Config via `pydantic-settings`, secrets never in the repo

A single typed `Settings` class read from environment variables. `APP_ENV` ∈
`{local, production}`. Locally values come from a git-ignored `.env`; in
production real environment variables are injected by the host. Only
`.env.example` is committed, with placeholders.

**Implemented guardrails** (`app/core/config.py`):
- Production refuses to boot with a placeholder or short signing key,
  `DEBUG=true`, or `CORS_ORIGINS` containing `*`.
- `DATABASE_URL` must use the `postgresql+asyncpg://` driver — catches the most
  common first-run mistake with a clear message.
- `.env` is read as UTF-8 explicitly (Windows would default to cp1252).

**Rule:** no `if app_env == "production"` inside business logic. Environments
differ by *which adapter is injected* (fake vs real SMS), not by branches in
service code.

### ADR-012 — Tooling: `uv`, `ruff`, `mypy`, `pytest`

`uv` for dependencies (fast, with a committed lockfile for reproducible
builds), `ruff` for lint + format (replaces flake8 + black + isort), `mypy` for
types, `pytest` + `httpx.AsyncClient` for tests, `pre-commit` to run them
automatically.

### ADR-013 — UUID primary keys

All tables use a UUID primary key (`uuid4`, generated in Python).

**Why not auto-increment integers:**
- Sequential ids are guessable — `/vehicles/124` invites trying 123 and 125,
  which turns any missed ownership check into a data leak.
- They leak business information: `/providers/57` reveals you have 57 providers.
- App-side generation simplifies tests and future data merging.

---

## 3. Current folder structure

Files marked ✅ exist and are verified; others are planned.

```
agri/
├── README.md                       ✅ quick start
├── pyproject.toml                  ✅ dependencies + ruff/mypy/pytest config
├── uv.lock                         ✅ exact dependency versions
├── alembic.ini                     ✅ migration config (no credentials)
├── .pre-commit-config.yaml         ✅ automatic pre-commit checks
├── .gitignore                      ✅ blocks .env and caches
├── .env.example                    ✅ committed template, placeholders only
├── .env                            ✅ your real local config (git-ignored)
│
├── docs/
│   ├── PROJECT.md                  ✅ this file
│   └── SETUP.md                    ✅ setup guide
├── mvp/
│   └── Agri_Vehicle_Rental_MVP.pdf ✅ original scope
│
├── app/
│   ├── main.py                     ✅ create_app() factory
│   ├── core/
│   │   ├── config.py               ✅ typed settings + production guardrails
│   │   ├── logging.py              ✅ structlog + phone/secret masking
│   │   ├── exceptions.py           ✅ error hierarchy + envelope handlers
│   │   ├── middleware.py           ✅ request id, timing, security headers
│   │   ├── rate_limit.py           ⬜ Phase 1
│   │   └── security.py             ⬜ Phase 1 (JWT, hashing)
│   ├── db/
│   │   ├── base.py                 ✅ DeclarativeBase, UUID + timestamp mixins
│   │   ├── session.py              ✅ async engine, get_db, check_database
│   │   ├── models.py               ✅ model registry for Alembic
│   │   └── migrations/
│   │       ├── env.py              ✅ reads URL from Settings; ignores PostGIS tables
│   │       ├── script.py.mako      ✅ migration template
│   │       └── versions/
│   │           └── ..._enable_postgis.py  ✅ first migration
│   ├── modules/                    ⬜ auth, users, providers, vehicles, media,
│   │                                  verification, search, calls, admin
│   ├── integrations/               ⬜ sms/ (port + fake + real), storage/
│   └── api/
│       ├── health.py               ✅ /health, /ready
│       └── v1/router.py            ✅ mounts feature routers
│
└── tests/
    ├── conftest.py                 ✅ app + client fixtures
    └── test_health.py              ✅ 8 tests passing
```

**Why feature modules rather than top-level `routers/`, `models/`, `services/`:**
with technical layers, adding one feature touches five distant folders and every
folder grows forever. With feature modules, a feature is one directory you can
read, test, or extract on its own.

---

## 4. Database & API decisions

### 4.1 Planned schema (MVP)

Conventions: UUID primary keys · `created_at`/`updated_at` everywhere ·
soft delete where history matters · Postgres enum types · UTC timestamps.

| Table | Purpose | Key columns |
|---|---|---|
| `users` | Identity for renters & providers | `phone_e164` **unique**, `role`, `status`, `phone_verified_at` |
| `user_profiles` | Renter details | `user_id`, `full_name`, `city`, `preferred_language` |
| `provider_profiles` | Provider details | `user_id`, `display_name`, `address`, **`location geography(Point,4326)`**, `service_radius_km`, `verification_status`, `verified_at`, `verified_by` |
| `vehicle_types` | Seeded taxonomy | `code`, `name_en`, `name_ta`, `icon` — reference data, not free text |
| `vehicles` | Listings | `provider_id`, `vehicle_type_id`, `registration_number`, `make`, `model`, `year`, `capacity`, `price_amount`, `price_unit`, `is_available`, `listing_status` |
| `vehicle_photos` | Images | `vehicle_id`, `object_key`, `thumb_key`, `sort_order` |
| `documents` | KYC files | `owner_type`, `owner_id`, `doc_type`, `object_key`, `status`, `reviewed_by`, `rejection_reason` |
| `verification_events` | Immutable approval audit | `subject_type`, `subject_id`, `from_status`, `to_status`, `actor_id`, `reason` |
| `otp_requests` | OTP state *(was Redis — ADR-010)* | `phone_e164`, `code_hash`, `attempts`, `expires_at`, `consumed_at` |
| `refresh_tokens` | Sessions | `user_id`, `token_hash`, `family_id`, `expires_at`, `revoked_at` |
| `call_sessions` | Every masked-call attempt | `caller_id`, `provider_id`, `vehicle_id`, `vendor_call_sid`, `status`, `duration_sec`, `idempotency_key` |
| `audit_logs` | Admin/sensitive actions | `actor_id`, `action`, `entity`, `entity_id`, `metadata` JSONB, `ip` |

### 4.2 Indexes that must exist

```sql
CREATE INDEX ix_provider_location ON provider_profiles USING GIST (location);

-- partial index: search only ever looks at discoverable providers
CREATE INDEX ix_provider_discoverable ON provider_profiles (verification_status)
  WHERE verification_status = 'APPROVED' AND deleted_at IS NULL;

CREATE INDEX ix_vehicles_available ON vehicles (provider_id, is_available)
  WHERE listing_status = 'APPROVED';
```

### 4.3 Verification state machine

The gate that makes R9 true — only eligible providers are discoverable:

```
DRAFT ──submit──▶ PENDING_REVIEW ──approve──▶ APPROVED ──suspend──▶ SUSPENDED
                        │                                              │
                        └──reject──▶ REJECTED ──resubmit──▶ PENDING_REVIEW
```

Transitions are legal only via the service layer, and each writes a
`verification_events` row. Search filters on `APPROVED`. Provider and vehicle
carry separate statuses — a verified provider can still have an unapproved
listing.

### 4.4 API conventions

- **Versioned from day one:** everything under `/api/v1/`. Mobile apps can't be
  force-updated; v1 clients will call v1 endpoints for months.
- **One error envelope** (implemented in `app/core/exceptions.py`):
  ```json
  { "error": { "code": "OTP_EXPIRED", "message": "...",
               "details": {}, "request_id": "0f9c1e4a" } }
  ```
  A stable machine-readable `code` means Flutter can branch on errors without
  parsing English prose.
- **Explicit `response_model` on every route** — enforces ADR-009.
- Pagination on every list endpoint; search uses cursor pagination by distance.
- snake_case JSON, ISO-8601 UTC timestamps, money as integer minor units (paise).
- `/health` and `/ready` unversioned — infrastructure, not API contract.
- Docs served locally, disabled in production.

### 4.5 Planned endpoint surface

```
POST   /api/v1/auth/otp/request           # rate limited
POST   /api/v1/auth/otp/verify            # -> access + refresh tokens
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout

GET    /api/v1/me
PATCH  /api/v1/me/renter-profile
PATCH  /api/v1/me/provider-profile        # includes location + service radius

GET    /api/v1/vehicle-types
POST   /api/v1/provider/vehicles
GET    /api/v1/provider/vehicles
PATCH  /api/v1/provider/vehicles/{id}
PATCH  /api/v1/provider/vehicles/{id}/availability
POST   /api/v1/provider/vehicles/{id}/photos
POST   /api/v1/provider/documents
POST   /api/v1/provider/submit-verification
GET    /api/v1/provider/verification-status

GET    /api/v1/search/providers           # lat, lng, radius_km, vehicle_type, cursor
GET    /api/v1/listings/{vehicle_id}      # NO phone number in response

POST   /api/v1/calls/initiate             # + Idempotency-Key

POST   /api/v1/admin/auth/login
GET    /api/v1/admin/verifications
POST   /api/v1/admin/verifications/{id}/approve
POST   /api/v1/admin/verifications/{id}/reject
POST   /api/v1/admin/providers            # manual onboarding (R10)
```

---

## 5. Environment setup

Two environments. **Revised** from an earlier three-environment plan (local /
SIT / production) — SIT testing happens on the local database instead.

| | **local** | **production** |
|---|---|---|
| `APP_ENV` | `local` | `production` |
| Runs on | your machine | TBD — not yet decided |
| Purpose | development **and** testing | live users |
| Database | native PostgreSQL 16 + PostGIS | dedicated instance, backups + PITR |
| SMS | **fake** — OTP printed to terminal | live vendor account |
| Telephony | not built yet | vendor TBD |
| Storage | local folder | object storage |
| Secrets from | git-ignored `.env` | injected env vars from a secret manager |
| `/docs` | enabled | **disabled** |
| Logs | DEBUG, human-readable | INFO, JSON |
| Migrations | run manually | explicit gated deploy step |

### Non-negotiable rules

1. **No secret is ever committed.** `.gitignore` blocks `.env*` except
   `*.example`; `.pre-commit-config.yaml` blocks it again at commit time.
2. **No environment branching in business logic** — inject a different adapter
   instead.
3. **Production credentials never exist on a developer machine.**
4. A missing or malformed required setting **crashes at startup**, loudly.
5. In production the app database user gets DML rights only; DDL is applied by a
   separate migration role. (Locally the `agri` user is superuser so it can
   install PostGIS — acceptable on a laptop only.)

Machine-level steps: `docs/SETUP.md`.

---

## 6. Security considerations

Ordered by real risk to this product.

### 6.1 Phone number privacy — the core product promise
- Never serialised into any renter-facing response (ADR-009).
- Contact only through the masked-call endpoint.
- **Implemented:** every log line masks phone numbers (`+9198****3210`), and
  keys containing `secret`/`password`/`token` are redacted entirely.

### 6.2 OTP abuse and SMS pumping fraud
Every OTP costs money; unprotected, this is a budget-drain attack.
- 6 digits, **stored hashed**, 5-minute expiry, max 5 attempts then invalidated.
- Per-phone (3/hour) and per-IP limits; exponential backoff.
- Constant-time comparison; identical response whether or not the phone exists
  (no user enumeration).
- Alert on OTP volume or failure-rate spikes.

### 6.3 Broken object-level authorisation (BOLA)
The most likely security bug in this app: Provider A editing Provider B's
vehicle by changing an id in the URL.
- Every provider-scoped route resolves the object **and asserts ownership** in
  the service layer. Never trust a path or body id.
- Admin routes check an admin role against the database, not just a JWT claim.
- Integration tests must include explicit "other provider gets 404" cases.

### 6.4 Documents and KYC data (India DPDP Act 2023)
- Private storage, encryption at rest, short-lived read URLs, never public.
- Validate real content type (magic bytes, not extension); enforce max size.
- **Strip EXIF GPS** from uploaded photos — otherwise uploads leak precise home
  coordinates.
- Prefer **not** storing Aadhaar. If unavoidable: encrypt, mask on display, log
  every access, confirm legal basis (Q8).
- Retention/deletion policy for rejected providers' documents.

### 6.5 Authentication & transport
- Access token 15 min; refresh rotated, hashed, revocable.
- Argon2id for admin passwords.
- HTTPS only, HSTS in production (already wired to `is_production`).
- **Implemented:** `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Cache-Control: no-store` on every response.
- CORS restricted to explicit origins; `*` rejected at startup in production.
- Vendor webhooks must verify signatures — they are unauthenticated by nature.

### 6.6 Input, output, dependencies
- All input through Pydantic; all output through explicit `response_model`.
- Parameterised queries only, including PostGIS calls.
- **Implemented:** unhandled exceptions return a generic message; the traceback
  goes to logs only. Leaking internals is free reconnaissance for an attacker.
- Idempotency keys on call initiation to prevent duplicate billable calls.
- `audit_logs` for every admin approve/reject/onboard action.
- Dependency and container scanning in CI.

---

## 7. Scalability & performance

### Scalability
- **Stateless app** → horizontal scaling behind a load balancer.
- Connection pooling per instance (`pool_pre_ping` on, so a dropped connection
  is replaced rather than handed to your code); PgBouncer before scaling past a
  handful of instances.
- GiST + partial indexes keep search fast as provider count grows.
- Redis for shared rate limits and caching (ADR-010 — required before
  multi-instance production).
- CDN for photos, not the API.
- Read replica for search later — the code path is already read-only.

### Performance — rural connectivity matters most
- **Generate thumbnails on upload.** Search results must reference small
  thumbs, not full-resolution originals. On patchy 3G this is the single
  biggest perceived-speed factor.
- Compute distance in the database; never post-process in Python.
- Avoid N+1 with `selectinload`; assert query counts in tests.
- Cursor pagination, small default page size.
- Response compression; statement and external-call timeouts everywhere.
- **Implemented:** every request is timed and logged with `duration_ms`, so p95
  latency per endpoint is measurable from day one.

---

## 8. Now vs later

### Build now (MVP)
Foundation ✅ · OTP auth + JWT/refresh · renter & provider profiles · media
upload · vehicle taxonomy + listing CRUD · document upload · verification state
machine + admin review + audit · PostGIS radius search with pagination ·
listing detail · masked-call initiation · rate limiting · migrations ·
integration tests for auth, ownership, search and verification.

### Add later (seams exist, don't build)
Redis-backed rate limiting · object storage · push notifications · booking &
availability calendar · payments · ratings & reviews · chat · background workers
· Elasticsearch · read replicas · analytics dashboards · multi-language content
· jobs/classifieds/real-estate modules.

### Deliberately NOT doing
Microservices · Kafka · Kubernetes · GraphQL · CQRS/event sourcing · service
mesh · multi-region · custom OAuth server · hand-rolled admin SPA.

Each costs real complexity now for benefits this pilot won't reach — and the
modular monolith keeps the door open for all of them.

---

## 9. Assumptions

Building on these unless corrected. Each is a place the design could shift.

| # | Assumption |
|---|---|
| A1 | Auth is phone + OTP; email is never a login credential. |
| A2 | One person can hold both renter and provider roles on a single phone-number account. |
| A3 | Search returns providers as the primary card with vehicles nested; renter filters by vehicle type. *(See Q6 — leaning vehicle-centric.)* |
| A4 | MVP availability is a simple `is_available` toggle — **no date/time calendar**. |
| A5 | Price is indicative display only; no payments in MVP. |
| A6 | Admin uses API endpoints consumed by a minimal internal tool; no admin SPA in backend scope. |
| A7 | Providers are notified of approval/rejection by SMS (push is out of scope). |
| A8 | Single country (India), currency INR, timestamps stored UTC. |
| A9 | Pilot scale: low thousands of providers, tens of thousands of users; architecture targets 10×. |
| A10 | Flutter handles location permission and sends lat/lng; no server-side geocoding in MVP. |
| A11 | Backend is API-only; no server-rendered pages. |

---

## 10. Open questions

### Blocking — needed before the affected module is built

| # | Question | Why it matters | Blocks |
|---|---|---|---|
| Q1 | Confirm phone+OTP auth (A1)? | Determines the whole identity model | Auth |
| Q2 | Which **SMS provider**, and is an account available? | Hard dependency for real login | Auth (real adapter) |
| Q3 | Is **DLT registration** (TRAI) done for sender ID + OTP template? | In India transactional SMS is undeliverable without it, and approval can take **weeks** — a schedule risk, not a code problem | Auth (production) |
| Q4 | Which **masked-calling vendor**, and what per-minute budget? | The MVP's headline feature can't be built or tested without it | Calls |
| Q5 | Are calls **recorded**? If so, consent + retention policy needed. | Legal exposure | Calls |
| Q6 | Is the discoverable unit a **provider** or an individual **vehicle**? | Changes the search query, response shape and Flutter cards | Search |
| Q7 | Exact **mandatory document list** (RC book only? licence? ID proof?) | Defines the `doc_type` enum and approval checklist | Verification |
| Q8 | Is any **government ID number** (Aadhaar/PAN) stored, or only images? | Aadhaar storage carries specific legal obligations | Verification |
| Q9 | Who are the **admins**, how do they log in, is 2FA required? | Admins can approve providers and see all PII | Admin |

### Important — has a sensible default

| # | Question | Proposed default |
|---|---|---|
| Q10 | Radius fixed by config or user-selectable? | Config default **25 km**; user picks 5/10/25/50; hard max 100 |
| Q11 | Must a renter log in to search? | Browsing anonymous, **login required to call** |
| Q12 | Fixed list of vehicle types — please supply it | Seeded table (tractor, harvester, rotavator, tiller…); admin can extend |
| Q13 | Pricing units: hour / acre / day / trip? | Support all four via a `price_unit` enum |
| Q14 | Which languages must the API return? | English + Tamil; taxonomy tables carry translated names |
| Q15 | Max photos per vehicle; max document size? | 6 photos @ 5 MB; documents 10 MB |
| Q16 | Where will production run? | Not decided — deferred by agreement |
| Q17 | Retention for rejected providers' documents? | Delete 90 days after final rejection |
| Q18 | Does a listing need separate approval from the provider? | Separate statuses; provider approval auto-approves the first listing |
| Q19 | Expected pilot volumes? | A9 until told otherwise |
| Q20 | CI platform? | GitHub Actions assumed, not yet set up |

---

## 11. Task board

⬜ not started · 🟡 in progress · ✅ done · ⛔ blocked

### Phase 0 — Foundation ✅ COMPLETE
| | Task |
|---|---|
| ✅ | `pyproject.toml` with dependencies + ruff/mypy/pytest config |
| ✅ | `.gitignore` protecting `.env` and caches |
| ✅ | `Settings` with typed validation + production guardrails |
| ✅ | `.env.example` template and local `.env` with generated secret |
| ✅ | Async SQLAlchemy engine, session factory, `get_db` dependency |
| ✅ | Declarative base, UUID + timestamp mixins, constraint naming convention |
| ✅ | Alembic configured (URL from Settings, PostGIS tables ignored) |
| ✅ | First migration: enable PostGIS |
| ✅ | `create_app()` factory with lifespan |
| ✅ | Error envelope + handlers (AppError, validation, HTTP, unhandled) |
| ✅ | structlog logging with phone/secret masking |
| ✅ | Request-id, timing and security-header middleware |
| ✅ | `/health` and `/ready` |
| ✅ | Test fixtures + 8 passing tests; ruff and mypy clean |
| ✅ | `.pre-commit-config.yaml` |
| ✅ | README + setup guide |
| ⬜ | Install PostgreSQL + PostGIS locally, run `alembic upgrade head` |
| ⬜ | CI pipeline (deferred — Q20) |

### Phase 1 — Authentication (next)  ⚠️ *real SMS needs Q2/Q3; fake adapter unblocks everything*
| | Task |
|---|---|
| ⬜ | `users` model + migration, E.164 phone validation |
| ⬜ | `otp_requests` model + migration |
| ⬜ | SMS port + fake adapter (logs the OTP) |
| ⬜ | OTP request/verify: hashing, expiry, attempt limits |
| ⬜ | Rate limiter (in-process now, Redis later) applied to OTP routes |
| ⬜ | `security.py`: JWT issue/verify, Argon2 hashing |
| ⬜ | Refresh token rotation + reuse detection |
| ⬜ | `get_current_user` / `require_role` dependencies |
| ⬜ | Auth integration tests including abuse cases |

### Phase 2 — Profiles
| | Task |
|---|---|
| ⬜ | Renter profile CRUD |
| ⬜ | Provider profile CRUD with geography point + service radius |
| ⬜ | Profile-completion flags for app routing (PDF §5.1 step 5) |

### Phase 3 — Media  ⛔ *Q15*
| | Task |
|---|---|
| ⬜ | Storage port + local-disk adapter |
| ⬜ | Upload validation (real content type, size limits) |
| ⬜ | EXIF stripping + thumbnail generation |

### Phase 4 — Vehicle listings  ⛔ *Q12, Q13*
| | Task |
|---|---|
| ⬜ | `vehicle_types` seed script |
| ⬜ | Vehicle CRUD with ownership enforcement |
| ⬜ | Photo attach/reorder/delete; availability toggle |

### Phase 5 — Verification & admin  ⛔ *Q7, Q8, Q9, Q18*
| | Task |
|---|---|
| ⬜ | `documents` model + upload linkage |
| ⬜ | Verification state machine with guarded transitions |
| ⬜ | Admin auth; review queue; approve/reject with reason |
| ⬜ | Manual provider onboarding (R10) |
| ⬜ | `audit_logs` + `verification_events` |
| ⬜ | Approval/rejection SMS notification (A7) |

### Phase 6 — Search & discovery  ⛔ *Q6, Q10*
| | Task |
|---|---|
| ⬜ | GiST + partial indexes migration |
| ⬜ | `ST_DWithin` radius query, distance-sorted, cursor-paginated |
| ⬜ | Vehicle-type filtering; listing detail (phone-free) |
| ⬜ | Index-usage (`EXPLAIN`) and query-count tests |

### Phase 7 — Protected calling  ⛔ *Q4, Q5*
| | Task |
|---|---|
| ⬜ | Telephony port + fake adapter |
| ⬜ | Vendor adapter once selected |
| ⬜ | `POST /calls/initiate` with idempotency + rate limit |
| ⬜ | `call_sessions` lifecycle + signed status webhook |
| ⬜ | Test asserting no phone number in any renter-facing response |

### Phase 8 — Hardening & production readiness
| | Task |
|---|---|
| ⬜ | **Swap in Redis** for rate limiting + OTP state (ADR-010) |
| ⬜ | Object storage adapter (ADR-007) |
| ⬜ | Error tracking + metrics + alerting |
| ⬜ | Load test the search endpoint |
| ⬜ | Production hosting decision (Q16), secret manager, backups + restore drill |
| ⬜ | Gated migration deploy step; rollback runbook |
| ⬜ | API docs handoff to the Flutter team |

---

## 12. Completed work

| Date | Item |
|---|---|
| 2026-08-12 | Inspected repository; extracted and analysed the MVP PDF |
| 2026-08-12 | Architecture proposal: ADRs, schema, endpoint surface, roadmap |
| 2026-08-12 | Created `docs/PROJECT.md` and `docs/SETUP.md` |
| 2026-08-13 | Scope decisions: two environments (not three); Redis deferred; telephony deferred; native PostgreSQL instead of Docker |
| 2026-08-13 | **Phase 0 built:** config with production guardrails, structured logging with PII masking, error envelope, middleware, async DB layer, Alembic + PostGIS migration, health/readiness, app factory |
| 2026-08-13 | Verified: 8 tests passing, `ruff check` clean, `mypy` clean, Alembic generates correct SQL |
| 2026-08-13 | `README.md`, `.pre-commit-config.yaml`; docs updated to match decisions |

## 13. Pending work

**Immediate:** install PostgreSQL + PostGIS (`docs/SETUP.md` §1), then
`alembic upgrade head`.

**Next:** Phase 1 — authentication. The fake SMS adapter means this can be
built and tested end to end without a vendor account.

Everything else: §11.

## 14. Future improvements

Beyond the PDF roadmap: OpenTelemetry tracing · blue/green deploys · contract
tests shared with the Flutter team · search relevance tuning (distance + rating
+ recency) · fraud scoring for fake listings · WhatsApp Business API
notifications (high engagement in Tier 2 markets) · offline-first sync ·
provider earnings analytics.

---

## Changelog

| Date | Change |
|---|---|
| 2026-08-12 | Initial architecture proposal |
| 2026-08-13 | Revised to two environments; ADR-010 revised (Redis deferred); ADR-007/008 marked deferred; ADR-013 added (UUID keys); Phase 0 completed and verified |
