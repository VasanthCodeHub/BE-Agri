# AgriAPP — what the mobile app needs from the API

Checked against the live server on 21 Aug 2026 after the backend rework
(bookings removed; contact/call, vehicle master data, profile, notifications,
reviews and favourites added).

The catalogue, provider vehicle management, contact, master data, profile,
favourites, notifications, reviews and the provider dashboard are wired to the
real API and working. Everything listed here is either blocked, faked, or
degraded.

## Start here

If only one thing gets done, make it **P0** — it is small and it currently stops
the app dead.

| | What | Why it matters |
|---|---|---|
| **P0** | Cloudinary credentials + signed upload preset not configured | `POST /provider/uploads/signature` returns `503 UPLOADS_NOT_CONFIGURED`, so nothing can be created and the catalogue stays empty |
| **P1** | Radius search on the feed | The core premise of the product; params exist but the app does not send them |
| **P1** | `completed_rentals` on the card | Cannot exist — the product has no bookings, so there is no rental history to count. Remove the UI field or display nothing |
| **P2** | Vehicle master data seeded in the target DB | The dropdown cascade (manufacturer → model → variant) is empty until `seed_master_data.py` is run against the real database |

---

## P0 — the app cannot create anything

Image uploads are switched off on this server (Cloudinary is not configured):

```
POST /api/v1/provider/uploads/signature  →  503
{"error":{"code":"UPLOADS_NOT_CONFIGURED",
          "message":"Image uploads are not available on this server yet."}}
```

And a listing cannot be created without at least one image
(`image_public_ids` requires 1–6 items). So until Cloudinary is configured, no
vehicle can be created by anyone and the feed is empty.

Fix: configure `CLOUDINARY_*` in `.env` and create the signed upload preset
(see `docs/SETUP.md` §2.6). Nothing else blocks creation.

---

## P1 — needed for the MVP as scoped

### Radius search on the feed

`GET /vehicles` supports `lat` + `lng` + `radius_km` and `sort=distance` — the
backend does the ST_DWithin search and returns `distance_km` per item. The app
does not send the device's coordinates yet, so every feed request today is
unfiltered and `distance_km` is null.

Send `lat`, `lng`, `radius_km` (and optionally `sort=distance`) on every feed
call once the app has location permission.

### `completed_rentals` on the vehicle card

The details screen renders a "completed rentals" trust signal. The product has
**no bookings** (scope decision, confirmed 21 Aug 2026), so this number can
never exist. Drop the field from the UI — the review count and average rating
already fill the trust slot.

---

## P2 — vehicle master data

`GET /api/v1/vehicle-masters?type_code=TRACTOR` returns the cascade:

```
vehicle_manufacturers
└── vehicle_models        (fuel_type, power_hp, vehicle_type_code)
    └── vehicle_variants  (manufacture_year, power_hp)
```

The endpoints exist and the code is tested; what is missing is **data**: the
seed script (`scripts/seed_master_data.py`) must be run against the target
database. It is idempotent — safe to re-run.

---

## Done since the 17 Aug check (the rework)

| Feature | Endpoint(s) |
|---|---|
| Contact / call (replaces masked calling) | `POST /api/v1/contact/call` → returns the provider's real E.164 number to dial, records the call, notifies the provider (`CALL_INITIATED`) |
| Vehicle master data | `GET /api/v1/vehicle-masters` |
| Profile | `GET /api/v1/me` (session check), `PATCH /api/v1/me` (name, email, address, location) |
| Favourites | `POST /api/v1/vehicles/{id}/favourite`, `DELETE /api/v1/vehicles/{id}/favourite`, `GET /api/v1/favourites` |
| Reviews | `GET` / `POST /api/v1/vehicles/{id}/reviews` |
| Notifications | `GET /api/v1/notifications`, `PATCH /api/v1/notifications/{id}/read`, `PATCH /api/v1/notifications/read-all` |
| Provider dashboard | `GET /api/v1/provider/summary` (totals, availability, favourites, calls, review stats) |
| Feed stats | `rating`, `review_count` on every card; `distance_km` when lat/lng sent |

**Bookings are gone, deliberately.** The six booking screens (availability,
summary, confirmation, my bookings, details, provider requests) must be removed
from the app — there is no booking system in the product anymore. The backend
bookings tables were dropped in migration `b7c2d4e6f8a0`.

**The role is now `USER` (was `RENTER`).** Tokens and OTP requests carry
`role: "USER"` or `"PROVIDER"`. Old `RENTER` values in the database are renamed
by the migration.

---

## Questions, not requests

- **`/vehicle-types` carries Tamil names and we are discarding them.** Nine of
  the twelve types have a `name_ta` (டிராக்டர், அறுவடை இயந்திரம், கலப்பை …).
  The audience is Tamil-speaking farmers with low digital literacy and the data
  already exists, but the app has no localisation layer. Worth a product
  decision — and three types have no Tamil name yet (`BALER`, `LEVELLER`,
  `WATER_TANKER`).
- **`GET /provider/vehicles` scoping** — confirmed scoped to the token's
  provider; a stranger's vehicle id returns 404.
- **Provider phone exposure is now intentional.** `POST /contact/call` returns
  the provider's real number so the caller can dial it directly. This replaces
  the old privacy promise of masked calling — confirm the product wants the
  real number exposed after a successful call record, or keep the number hidden
  and re-introduce a proxy later.
- **`price_unit: TRIP`** is in the enum — is it actually offered? With no
  bookings it only affects the feed display.

---

## Confirmed working

Verified end to end against the running server on 21 Aug 2026.

- **Auth** — OTP request / verify / refresh / logout / me, and `GET /me` in
  the profile module. Test code `0000`.
- **Catalogue** — `GET /vehicles`, `/vehicles/{id}`, `/vehicle-types`,
  `/vehicle-masters`. These are **public**, so browsing works signed-out.
- **Provider vehicles** — list, create, update, delete, availability toggle.
  (Create is blocked in practice by the P0 Cloudinary rule above.)
- **Contact** — `POST /contact/call` records the call and returns the
  provider's number.
- **Social** — favourites, reviews, notifications, profile update, provider
  summary.

Notes worth keeping for whoever tests next:

- The OTP code is **single-use** — each verify needs a fresh
  `POST /auth/otp/request`, or the second attempt returns no token.
- The `role` sent on the OTP **request** sets `active_role` on the token.
  Requesting with `PROVIDER` yields a provider session.
- Access token 900s, refresh 30 days.
- Master-data fields on a vehicle (`manufacturer_id`, `model_id`, `variant_id`)
  are optional; when given, the backend validates the combination and overwrites
  `brand`/`model` with the canonical master names.

## One note on error handling

The API does not use FastAPI's default `{"detail": ...}`. Every failure is:

```json
{"error": {"code": "VALIDATION_ERROR",
           "message": "One or more fields are invalid.",
           "details": {"fields": [{"field": "...", "reason": "..."}]},
           "request_id": "f69f1c32c6f54289"}}
```

This is fine and arguably better — flagging it only because it is easy to get
wrong client-side, and because `request_id` is worth quoting when reporting a
bug back. Please keep the envelope consistent on any new endpoints.