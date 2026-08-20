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
| Database | PostgreSQL **18** running locally, `agri_local`, migrations at `b7c2d4e6f8a0` ✅ · PostGIS **not installed yet** |
| Status | **Phases 0, 1, 3, 4 + the app-rework complete** — auth, vehicle listings, master data, contact/call, favourites, reviews, notifications, profile and provider summary working. **Bookings removed by decision (2026-08-21)** |
| Last updated | 2026-08-21 |

---

## 1. Requirements

### 1.1 Product

A mobile app connecting renters who need agricultural vehicles with verified
local vehicle owners/drivers. Launch targets **Tier 2 cities**. One app, two
login experiences (User and Provider), plus admin operations.

### 1.2 Roles

| Role | MVP capabilities |
|---|---|---|
| **User** | Register/login, basic profile, search nearby, view listing, initiate call to a provider *(was "Renter" — renamed 2026-08-21)* |
| **Provider / Owner / Driver** | Register/login, provider profile, add vehicle, upload docs/photos, manage listing status |
| **Admin** | Manually onboard providers, review documents, approve/reject, support ops |

**Bookings are out of scope by product decision (2026-08-21):** the app has no
booking flow; users find a vehicle and call the provider directly. The
`bookings` table and `booking_status`/`session_block` enum types were dropped in
migration `b7c2d4e6f8a0`, and the `RENTER` role value was renamed to `USER` in
the same migration.

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

Primary auth is **phone (E.164) + 4-digit SMS OTP**. Passwords only for admins.

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

**Status: superseded for images by ADR-016.** Vehicle photos now go to
Cloudinary via signed direct uploads. The principle survives intact — files never
touch the app server, and the database stores only a key — but the mechanism is
Cloudinary's signature rather than S3 presigned URLs. Verification documents
(Phase 5) are still open; they are private, so they may need a different bucket
and stricter delivery than public listing photos.

### ADR-016 — Cloudinary signed direct uploads for vehicle photos

The Flutter app uploads photos **straight to Cloudinary**. This backend never
receives the image bytes; it only authorises each upload by signing it.

```
app ──"may I upload?"──▶ our API      (tiny JSON, PROVIDER token required)
app ◀──── signature ──── our API
app ──── the photo ────▶ Cloudinary   (big, and direct)
```

**Why not proxy the file through FastAPI:** the photo would travel twice, and a
provider on rural 4G would wait twice as long while a worker sat holding the
bytes. Cloudinary's edge is far closer to the user than our server will be.

**Why signed and not an unsigned upload preset:** an unsigned preset accepts
uploads from anyone. The cloud name and preset name both ship inside the APK, and
an APK is trivial to unpack — so a stranger could exhaust the quota or host their
own files on the account, with nothing recording who did it. A signature requires
a valid access token from this API first, and cannot be forged without the API
secret, which never leaves the server. It also expires.

**The backend chooses the `public_id`, not the client.** It is one of the signed
parameters, so it cannot be altered. Otherwise a caller could aim an upload at
`agri/documents/…` and overwrite a provider's verification papers.

**The database stores the `public_id`, never a URL.** Three consequences:

- **Sizes.** One id serves any dimension, so the feed requests 400px thumbnails
  while the detail screen gets full size. A stored URL would force every list
  card to download a full-resolution photo — on a rural connection that is the
  difference between a usable and an unusable app.
- **Verification.** An id inside our own folder is provably ours. An arbitrary
  URL could point anywhere, at content that changes or disappears.
- **Portability.** Moving off Cloudinary changes one URL-building function
  instead of every stored row.

**EXIF stripping is a requirement, not a nicety.** A photo taken in a provider's
yard carries their GPS position in its metadata. ADR-009 keeps provider locations
out of every renter-facing response; an unstripped photo would hand them over
anyway. Configured on the Cloudinary upload preset.

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

**Status: superseded by product decision (2026-08-21).** The new product
removes number masking: `POST /api/v1/contact/call` records the call and
returns the provider's real E.164 number for the caller to dial directly. The
port/interface below stays valid if masking returns. Candidates if it ever
does: Exotel (strongest India coverage), Twilio, Plivo, Knowlarity.

### ADR-009 — Provider phone numbers are never serialised to renters

No response schema reachable by a renter contains a provider's phone number.
Contact happens only via the masked-call endpoint. Enforced structurally: every
route declares an explicit `response_model`, and provider phone exists only on
the ORM model, never on a public read schema.

**Why:** this is the product's stated privacy promise (PDF §2.2, §5.2 step 12).
Relying on developers to remember to exclude a field will fail; relying on the
schema layer will not.

**Revised (2026-08-21):** the product now reveals the provider's number — but
only through `POST /api/v1/contact/call`, after the caller authenticates and
the call is recorded. The feed and listing detail remain phone-free, enforced
structurally as before.

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

### ADR-014 — One phone number, multiple roles

**Decision (client requirement, 2026-08-13):** a single user may hold both the
USER and PROVIDER roles (the role value was `RENTER` until it was renamed to
`USER` in migration `b7c2d4e6f8a0`, 2026-08-21). Roles live in a `user_roles`
table, not as a column on `users`, with `UNIQUE(user_id, role)`.

Selecting a role the user does not yet hold at login **grants** it rather than
rejecting the login. That is safe because the role by itself confers nothing —
becoming a discoverable provider still requires a provider profile and admin
verification.

**Why a table rather than two booleans:** ADMIN returns in Phase 5, and adding
a row beats adding a column. The unique constraint also makes "cannot hold the
same role twice" a database guarantee rather than a code convention.

**Constraint:** one **provider profile** per user — enforced in Phase 2 by a
unique constraint on `provider_profiles.user_id`. A provider may still list
multiple vehicles.

**ADMIN is absent from the enum for now.** When it returns, the login endpoint
must take a *narrower* input enum than the database stores, or a caller could
self-assign ADMIN through an unauthenticated endpoint.

### ADR-015 — Security side effects commit before raising

**Decision:** the three places that write and then deliberately return an error
call `repo.commit()` first: recording a failed OTP attempt, burning an
exhausted OTP, and revoking a stolen token family.

**Why:** `get_db` gives each request one transaction and rolls it back on any
exception. That is correct for ordinary writes — it stops half-created records.
But it silently discards writes made just before an error response.

**This was found by testing, not by reading.** Both protections looked correct
and did nothing:

| Write | Then | Actual result |
|---|---|---|
| `attempts += 1` | raise `OTP_INVALID` | counter rolled back → **unlimited guesses at a 4-digit code** |
| `revoke_family()` | raise `TOKEN_REUSED` | revocation rolled back → **stolen token still worked** |

Both are covered by regression tests now (`test_wrong_code_decrements_remaining_attempts`,
`test_reusing_a_rotated_token_revokes_the_whole_family`). The general lesson:
**a security control that is never observed failing has not been verified.**

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
│   │   ├── phone.py                ✅ E.164 normalisation (Indian mobiles)
│   │   ├── rate_limit.py           ⬜ deferred (ADR-010)
│   │   └── security.py             ✅ JWT, Argon2 OTP hashing, refresh tokens
│   ├── db/
│   │   ├── base.py                 ✅ DeclarativeBase, UUID + timestamp mixins
│   │   ├── session.py              ✅ async engine, get_db, check_database
│   │   ├── models.py               ✅ model registry for Alembic
│   │   └── migrations/
│   │       ├── env.py              ✅ reads URL from Settings; ignores PostGIS tables
│   │       ├── script.py.mako      ✅ migration template
│   │       └── versions/
│   │           ├── ..._enable_postgis.py         ✅ extensions
│   │           ├── ..._add_users_roles_otp_...py  ✅ identity + auth tables
│   │           ├── ..._add_vehicle_types_...py    ✅ listings + seeded taxonomy
│   │           └── ..._store_cloudinary_public_id ✅ images keyed by public_id
│   ├── modules/
│   │   ├── auth/                   ✅ router, service, repository, schemas, deps
│   │   ├── users/                  ✅ models only (profiles = Phase 2)
│   │   ├── vehicles/               ✅ full CRUD + public feed + registration rules
│   │   ├── uploads/                ✅ Cloudinary signature endpoint
│   │   └── providers, verification, search, calls, admin   ⬜
│   ├── integrations/
│   │   ├── sms/                    ✅ port + fake + twilio
│   │   └── cloudinary.py           ✅ upload signing + delivery URLs
│   └── api/
│       ├── health.py               ✅ /health, /ready
│       └── v1/router.py            ✅ mounts feature routers
│
└── tests/
    ├── conftest.py                 ✅ app + client + rolled-back session fixtures
    ├── test_health.py              ✅
    ├── test_auth.py                ✅
    ├── test_sms_twilio.py          ✅
    ├── test_uploads.py             ✅
    └── test_vehicles.py            ✅  112 tests total
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
| `vehicle_types` ✅ | Seeded taxonomy | `code`, `name_en`, `name_ta`, `sort_order`, `is_active` — reference data, not free text. 12 provisional rows seeded by migration; **Tamil names need a native speaker's review** |
| `vehicles` ✅ | Listings | `provider_user_id`, `vehicle_type_id`, `registration_number` (**unique among live rows**), `name`, `brand`, `model`, `manufacture_year`, `note`, `price_amount` (paise), `price_unit`, `location_text`, `latitude`/`longitude` (nullable), `fuel_type`, `power_hp`, `transmission`, `is_available`, `listing_status`, `deleted_at`, `manufacturer_id`/`model_id`/`variant_id` (nullable master refs, `SET NULL`) |
| `vehicle_images` ✅ | Photos | `vehicle_id`, **`public_id`** (Cloudinary), `sort_order` — URLs are derived, not stored (ADR-016) |
| `vehicle_manufacturers` ✅ | Master data | `name` unique, `sort_order`, `is_active` — seeded by `scripts/seed_master_data.py` |
| `vehicle_models` ✅ | Master data | `manufacturer_id`, `name`, `vehicle_type_id`, `fuel_type`, `power_hp`; unique per manufacturer |
| `vehicle_variants` ✅ | Master data | `model_id`, `name`, `manufacture_year`, `power_hp`; unique per model |
| `contact_calls` ✅ | Direct-call log | `caller_user_id`, `provider_user_id`, `vehicle_id`, `created_at` — the dashboard's interest signal now that bookings are gone |
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
✅ POST   /api/v1/auth/otp/request           # rate limiting still deferred
✅ POST   /api/v1/auth/otp/verify            # -> access + refresh tokens
✅ POST   /api/v1/auth/refresh
✅ POST   /api/v1/auth/logout
✅ GET    /api/v1/auth/me

⬜ PATCH  /api/v1/me/renter-profile
⬜ PATCH  /api/v1/me/provider-profile        # includes location + service radius

✅ GET    /api/v1/vehicle-types              # seeded taxonomy
✅ GET    /api/v1/vehicle-masters             # manufacturer → model → variant cascade
✅ POST   /api/v1/contact/call                # record a call, return the provider's number
✅ GET    /api/v1/me                          # session check (profile module)
✅ PATCH  /api/v1/me                          # name, email, address, location
✅ GET    /api/v1/provider/summary            # dashboard stats, no bookings
✅ GET/POST /api/v1/vehicles/{id}/favourite, GET /api/v1/favourites
✅ GET/POST /api/v1/vehicles/{id}/reviews
✅ GET    /api/v1/notifications + read + read-all
✅ POST   /api/v1/provider/uploads/signature # authorises a direct Cloudinary upload
✅ POST   /api/v1/provider/vehicles
✅ GET    /api/v1/provider/vehicles
✅ GET    /api/v1/provider/vehicles/{id}     # owner view, for the edit screen
✅ PATCH  /api/v1/provider/vehicles/{id}     # partial
✅ PATCH  /api/v1/provider/vehicles/{id}/availability
✅ DELETE /api/v1/provider/vehicles/{id}     # soft
✅ GET    /api/v1/vehicles                   # public feed, paginated, type filter
✅ GET    /api/v1/vehicles/{id}              # public detail, NO phone number

⬜ POST   /api/v1/provider/documents
⬜ POST   /api/v1/provider/submit-verification
⬜ GET    /api/v1/provider/verification-status

⬜ GET    /api/v1/search/providers           # lat, lng, radius_km, vehicle_type, cursor
✅ POST   /api/v1/contact/call               # direct call record (was masked /calls/initiate)

⬜ POST   /api/v1/admin/auth/login
⬜ GET    /api/v1/admin/verifications
⬜ POST   /api/v1/admin/verifications/{id}/approve
⬜ POST   /api/v1/admin/verifications/{id}/reject
⬜ POST   /api/v1/admin/providers            # manual onboarding (R10)
```

Two names changed from the original plan. `POST /provider/vehicles/{id}/photos`
never appeared: photos are attached by passing Cloudinary `public_id`s to the
create/edit endpoints, because the app uploads directly (ADR-016). And listing
detail is `GET /vehicles/{id}` rather than `/listings/{id}`, so the public feed
and the item it returns share a prefix.

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
- 4 digits, **stored hashed**, 5-minute expiry, max 5 attempts then invalidated.
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
| A2 | ✅ **Confirmed by client 2026-08-13:** one person holds both renter and provider roles on a single phone-number account, but only **one provider profile**. See ADR-014. |
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
| Q2 | ~~Which **SMS provider**?~~ **Answered 2026-08-14: Twilio.** Adapter built and credentials verified. The account is on the **trial tier**, which cannot send custom text (`572006`) — it must be upgraded before any real OTP can be delivered | Hard dependency for real login | ~~Auth~~ → account upgrade |
| Q3 | Is **DLT registration** (TRAI) done for sender ID + OTP template? **Still open, and now the critical path.** `SMS_OTP_TEMPLATE` is configurable precisely so it can be made to match the registered template character for character | In India transactional SMS is undeliverable without it, and approval can take **weeks** — a schedule risk, not a code problem | Auth (production) |
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
| Q12 | Fixed list of vehicle types — please supply it | **12 provisional types seeded** (tractor, power tiller, harvester, rotavator, plough, seed drill, sprayer, thresher, baler, leveller, trailer, water tanker). A table, not an enum, so the client's real list is a data change. **Tamil names need review** |
| Q13 | Pricing units: hour / acre / day / trip? | Support all four via a `price_unit` enum |
| Q14 | Which languages must the API return? | English + Tamil; taxonomy tables carry translated names |
| Q15 | Max photos per vehicle; max document size? | **Implemented: 1–6 photos per vehicle** (API-enforced). File size and format limits belong on the Cloudinary signed upload preset, not in our code. Documents still open |
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
| ✅ | Install PostgreSQL locally, create `agri_local`, run `alembic upgrade head` |
| ✅ | Git repository initialised and pushed to GitHub |
| ⬜ | `uv run pre-commit install` (hooks are configured, not yet activated) |
| ⬜ | Install PostGIS — **not needed until Phase 6**, see `docs/SETUP.md` §1.2 |
| ⬜ | CI pipeline (deferred — Q20) |

### Phase 1 — Authentication ✅ COMPLETE
| | Task |
|---|---|
| ✅ | `users` + `user_roles` models and migration |
| ✅ | E.164 phone normalisation (`app/core/phone.py`) |
| ✅ | `otp_requests` + `refresh_tokens` models and migration |
| ✅ | SMS port + fake adapter (prints the OTP to the terminal) |
| ✅ | OTP request/verify: Argon2 hashing, expiry, single use, attempt limits |
| ✅ | Dev bypass code `0000`, blocked at startup in production |
| ✅ | `security.py`: JWT issue/verify, Argon2, `secrets`-based token generation |
| ✅ | Refresh token rotation + reuse detection + family revocation |
| ✅ | `get_current_user`, `get_active_role`, `require_role` dependencies |
| ✅ | 5 endpoints: otp/request, otp/verify, refresh, logout, me |
| ✅ | 23 auth tests, incl. regression tests for the two rollback bugs |
| ⬜ | Rate limiting — **deferred to production** by agreement (ADR-010) |
| ✅ | Real SMS vendor adapter — **Twilio**, with startup credential validation |
| ⬜ | Real OTP delivery — blocked on upgrading the Twilio account off the trial tier, and on DLT (Q3) |

### Phase 2 — Profiles
| | Task |
|---|---|
| ⬜ | Renter profile CRUD |
| ⬜ | Provider profile CRUD with geography point + service radius |
| ⬜ | Profile-completion flags for app routing (PDF §5.1 step 5) |

### Phase 3 — Media ✅ COMPLETE *(via Cloudinary, ADR-016)*
| | Task |
|---|---|
| ✅ | ~~Storage port + local-disk adapter~~ → Cloudinary signed direct uploads. The app uploads straight to Cloudinary; image bytes never reach this backend |
| ✅ | `POST /provider/uploads/signature` — PROVIDER-only, backend-chosen `public_id`, expiring signature |
| ✅ | Upload validation — two layers: shape (schema, 422) and folder ownership (service, 400 `IMAGE_NOT_RECOGNISED`) |
| ✅ | Thumbnails — derived from `public_id` at request time (`w_400,c_fill,q_auto,f_auto`), so no generation step and no second stored file |
| ⚠️ | **EXIF stripping + size/format caps** — configured on the Cloudinary upload preset, which is a console task, not code. **Not yet done** |
| ⬜ | Orphan cleanup — a photo uploaded for a listing that is never created stays in Cloudinary. Cheap to ignore at pilot scale; worth a sweep later |

### Phase 4 — Vehicle listings ✅ COMPLETE
| | Task |
|---|---|
| ✅ | `vehicle_types` seeded **by migration**, not a script — `vehicles.vehicle_type_id` is NOT NULL, so an empty taxonomy means no listing can be created at all |
| ✅ | Vehicle CRUD with ownership enforcement — ownership is in the WHERE clause, and a stranger's id returns **404 not 403** so inventory cannot be enumerated |
| ✅ | Photo attach/reorder/delete — `image_public_ids` replaces the whole set, so reordering is just a new order |
| ✅ | Availability toggle, and soft delete that releases the registration number for re-listing |
| ✅ | Public feed + detail, both phone-free and RC-number-free (ADR-009) |
| ✅ | Registration numbers normalised and **unique among live listings**; state series and BH series both accepted |
| ✅ | DB check constraints on year, price, power and coordinates |
| ⬜ | Listing moderation — `listing_status` exists and defaults to `APPROVED` because no admin exists yet. Phase 5 flips the default to `DRAFT` and adds the state machine |

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

### Phase 7 — Contact & calling ✅ REPLACED
| | Task |
|---|---|
| ✅ | `POST /api/v1/contact/call` — any authenticated user calls a vehicle's provider; the call is recorded in `contact_calls` and the provider gets a `CALL_INITIATED` notification |
| ✅ | The provider's real number is returned **only** to the caller who initiated the call (direct dial per product decision 2026-08-21) |
| ⬜ | Re-introduce masked calling later if the product changes its mind — ADR-008 port is still valid |

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
| 2026-08-13 | PostgreSQL 18 installed; `agri` role + `agri_local` database created; migration `0001` applied (PostGIS skipped, not yet installed) |
| 2026-08-13 | Decision: **no separate test database** — tests run against `agri_local` inside always-rolled-back transactions |
| 2026-08-13 | Git repository initialised and pushed to <https://github.com/VasanthCodeHub/BE-Agri> (`.env` verified untracked) |
| 2026-08-13 | Client decisions: ADMIN dropped for now; one phone = both roles (ADR-014); one provider profile per user; rate limiting deferred to production |
| 2026-08-13 | **Phase 1 built:** 5 auth endpoints, 4 tables, phone normalisation, OTP with Argon2 + expiry + attempt limits, JWT access tokens, rotating refresh tokens with reuse detection, dev bypass code |
| 2026-08-13 | Hand-corrected the autogenerated migration: missing `MetaData` import, enum created 3×, enums not dropped on downgrade. Verified with a downgrade/upgrade round-trip |
| 2026-08-13 | **Found and fixed two security bugs by end-to-end testing** — OTP attempt counter and token-family revocation were both being rolled back (ADR-015). Regression tests added |
| 2026-08-13 | 31 tests passing, ruff clean, mypy clean; test transactions verified to leave zero rows behind |
| 2026-08-14 | **OTP shortened to 4 digits.** Only 10,000 possibilities, so the attempt limit is what keeps guessing impractical — noted in `core/security.py` |
| 2026-08-14 | **Twilio SMS adapter built** (Q2 answered). httpx rather than the official SDK, which is synchronous and would block the event loop for the whole round trip on every OTP |
| 2026-08-14 | Startup guards for Twilio: partial config, Account SID pasted into the auth-token field, tokens that are not 32 hex characters, non-E.164 sender. Twilio answers all of those with a bare `20003` |
| 2026-08-14 | A failed SMS send is now `503 OTP_SEND_FAILED` instead of a bare 500, and the OTP row rolls back so no code is left behind that the user never received |
| 2026-08-14 | **Tested real SMS end to end and found a hard blocker:** the Twilio account is on the trial tier, which rejects custom message bodies (`572006`) and substitutes its own sample code. The credentials and sender number are verified working; only the tier is wrong. Recorded in `integrations/sms/twilio.py` and `SETUP.md` §2.5 |
| 2026-08-14 | Trial-tier trap worth remembering: `IncomingPhoneNumbers` returns an **empty list** on a trial account, which reads exactly like "the sender number is not on this account". It is not — the Messages log is the endpoint that tells the truth |
| 2026-08-14 | Swagger landing page generated **from settings**, so the documented OTP length and dev bypass can never drift from the running server |
| 2026-08-14 | **Phase 4 built:** 3 tables, 9 endpoints, seeded taxonomy, registration-number rules, DB check constraints, soft delete, public feed and detail with no phone or RC number |
| 2026-08-14 | Hand-corrected the vehicles migration for the same three autogenerate faults as Phase 1 (missing `MetaData` import, enums created more than once, enums not dropped on downgrade). Verified with a downgrade/upgrade round-trip |
| 2026-08-14 | Hit the `MissingGreenlet` trap on `PATCH availability`: `updated_at` carries `onupdate`, and PostgreSQL returns server defaults via `RETURNING` on INSERT but not on UPDATE, so reading it during serialisation lazy-loads. Fixed in the repository |
| 2026-08-14 | **ADR-016 — Cloudinary signed direct uploads.** `POST /provider/uploads/signature`; `vehicle_images` now stores `public_id` instead of a URL, so thumbnails are derived per request |
| 2026-08-14 | 112 tests passing, ruff clean, mypy clean; both new migrations verified reversible |
| 2026-08-21 | **Product rework:** bookings removed entirely (module + table + enum types dropped); role `RENTER` renamed to `USER`; vehicle master data added (manufacturers → models → variants, optional vehicle refs, `GET /vehicle-masters`); `POST /contact/call` replaces masked calling; `GET /me` + `PATCH /me`; provider summary rebuilt without bookings; favourites/reviews/notifications wired; migration `b7c2d4e6f8a0`; idempotent seed script `scripts/seed_master_data.py` |

## 13. Pending work

**Blocked on other people, not on code:**

| What | Blocked on |
|---|---|
| Real SMS OTP delivery | The Twilio account is on the **trial tier**, which cannot send custom text at all (error `572006`) — it must be upgraded. Credentials themselves are verified working |
| SMS to Indian handsets | **DLT registration** (Q3) — weeks of lead time, so a schedule risk |
| Real photo uploads | Cloudinary credentials + a signed upload preset (see `docs/SETUP.md` §2.6) |
| Verification & admin | Q7, Q8, Q9 — document list, whether ID numbers are stored, who the admins are |
| Masked calling | **Removed by decision 2026-08-21** — direct dial via `POST /contact/call`. Re-open if the product wants number masking back (ADR-008) |

**Ready to build now, needs nobody:**

1. **Phase 2 profiles** — `PATCH /me/renter-profile`, `PATCH /me/provider-profile`.
   The provider half needs **PostGIS**, which is still not installed. Note this
   contradicts the earlier "PostGIS from Phase 6" plan: the provider profile
   carries a `geography(Point)` column, so it is needed at Phase 2.
2. **OTP rate limiting** — deferred by agreement (ADR-010), but the config value
   and `count_otps_since()` already exist; it is one check in `request_otp`.
   Matters more now the OTP is 4 digits.

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
| 2026-08-13 | ADR-014 added (one phone, multiple roles); ADR-015 added (security side effects commit before raising); assumption A2 confirmed; Phase 1 completed |
| 2026-08-14 | OTP shortened to 4 digits; Twilio SMS adapter added (Q2 answered); ADR-016 added (Cloudinary signed direct uploads); Phase 4 vehicle listings completed; Phase 3 media covered by Cloudinary rather than local disk |
| 2026-08-21 | Rework per new requirements: bookings removed (module, table, enum types); role `RENTER` → `USER`; vehicle master data + `GET /vehicle-masters`; `POST /contact/call` direct-dial replacing masked calling (ADR-008/009 revised); `GET`/`PATCH /me`; provider summary without bookings; favourites, reviews and notifications wired; migration `b7c2d4e6f8a0`; seed script for master data |
