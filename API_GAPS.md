# AgriAPP — what the mobile app needs from the API

Checked against the live spec on 17 Aug 2026 (`openapi.json`, 15 paths, v0.1.0)
and by calling the running server.

The renter catalogue and provider vehicle management are wired to the real API
and working. Everything listed here is either blocked, faked, or degraded.

## Start here

If only one thing gets done, make it **P0** — it is small and it currently stops
the app dead.

| | What | Why it matters |
|---|---|---|
| **P0** | Uploads are off + a listing requires an image | Nothing can be created, so the catalogue is empty and the app shows blank screens |
| **P1** | Radius search params | The core premise of the product; not possible today |
| **P1** | Masked calling | Explicitly in the brief; no endpoint at all |
| **P1** | Missing vehicle fields | UI renders zeros for ratings and distance |
| **P2** | Bookings | 6 built screens on mock data — but see the scope question |
| **P3** | Favourites, notifications, reviews, profile editing | Whole features faked |

---

## P0 — the app cannot create anything

Two separate things that combine into a deadlock.

**1. Image uploads are switched off.**

```
POST /api/v1/provider/uploads/signature  →  503
{"error":{"code":"UPLOADS_NOT_CONFIGURED",
          "message":"Image uploads are not available on this server yet."}}
```

**2. A listing cannot be created without at least one image.**

```
POST /api/v1/provider/vehicles   with  "image_public_ids": []   →  422
{"field":"image_public_ids",
 "reason":"List should have at least 1 item after validation, not 0"}
```

Uploads are off, and creating a listing requires an uploaded image. So **no
vehicle can be created by anyone**, which is why `GET /vehicles` returns
`{"items":[],"total":0}` and the app has nothing to display.

Any one of these unblocks us:

- Configure Cloudinary — the proper fix
- Allow `image_public_ids` to be empty, so listings can be created now and
  photographed later
- Seed 5–10 listings server-side, so at least the renter side can be demoed

---

## P1 — needed for the MVP as scoped

### Search parameters

`GET /vehicles` accepts only `type_code`, `limit`, `offset`. Everything else is
filtered client-side over a single 100-row page, which breaks as soon as the
catalogue outgrows one page.

Needed: `lat` + `lng` + `radius_km`, `q` (text), `max_price`, `available_only`,
`sort`.

**`lat`/`lng`/`radius_km` is the important one.** Location-based discovery is the
product's core premise in the MVP scope document and is not currently possible.
The app already captures real device coordinates when a provider creates a
listing (`VehicleCreateIn.latitude` / `longitude`), so the data will be there.

### Masked calling

The brief says all calls are routed through the app so personal numbers stay
private. There is no endpoint, so this screen is a mock.

Needed: `POST /contact/call` taking a vehicle or provider id, returning a proxy
number to dial or a call id.

### Fields missing from vehicle responses

`VehicleCardOut` and `VehicleOut` are missing values the built UI displays. They
currently render as `0`, which reads as "0 stars, 0 km" rather than "unknown".

| Field | Used by |
|---|---|
| `rating`, `review_count` | Every card, search row, details header |
| `distance_km` | Search rows and "nearby" copy — needs the caller's lat/lng |
| `completed_rentals` | Details screen, provider trust signal |
| `provider_name` | Nullable on the card today; always needed |
| `provider_phone` | Masked calling |

---

## P2 — bookings

No endpoints exist. Six built screens run on mock data: availability, summary,
confirmation, my bookings, booking details, and the provider's incoming
requests.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/vehicles/{id}/availability?date=` | Which 4-hour sessions are free |
| `POST` | `/bookings` | Create a request |
| `GET` | `/bookings` | Renter's own, filterable by status |
| `GET` | `/bookings/{id}` | Detail + stage timeline |
| `GET` | `/provider/bookings` | Provider's incoming requests |
| `PATCH` | `/provider/bookings/{id}` | Accept / reject |
| `PATCH` | `/bookings/{id}/cancel` | Renter cancels |

A booking needs: vehicle, renter, date, session, duration, amount, status
(`PENDING` / `ACCEPTED` / `ACTIVE` / `COMPLETED` / `REJECTED` / `CANCELLED`),
a reference like `AGR-24817`, and optional renter notes.

**Decide before building:** the MVP scope document lists booking as *post-MVP*,
but the Figma has six booking screens drawn and the app has them built. These
two disagree and it is not the backend's call to make.

---

## P3 — features with no endpoints

- **Favourites** — `GET /favourites`, `POST` / `DELETE /vehicles/{id}/favourite`.
  The screen currently shows the first three listings as a placeholder.
- **Notifications** — `GET /notifications`, plus mark-read
- **Reviews** — `GET` / `POST /vehicles/{id}/reviews`
- **Profile editing** — only `GET /auth/me` exists; no way to update name,
  email or location
- **Provider dashboard stats** — total vehicles, active rentals, completed,
  lifetime earnings. Derivable from bookings once those exist, but one
  `GET /provider/summary` would save four calls on the dashboard.

---

## Questions, not requests

- **`/vehicle-types` carries Tamil names and we are discarding them.** Nine of
  the twelve types have a `name_ta` (டிராக்டர், அறுவடை இயந்திரம், கலப்பை …).
  The audience is Tamil-speaking farmers with low digital literacy and the data
  already exists, but the app has no localisation layer. Worth a product
  decision — and three types have no Tamil name yet (`BALER`, `LEVELLER`,
  `WATER_TANKER`).
- **`GET /provider/vehicles` scoping** — confirm it is scoped to the token's
  provider. The app assumes so.
- **Listing status transitions** — `ListingStatus` has `PENDING_REVIEW` and
  `APPROVED`, but nothing in the mobile API moves between them. Consistent with
  admin living in a separate web panel; please confirm that is the plan.
- **`price_unit: TRIP`** is in the enum — is it actually offered? It changes the
  booking maths.

---

## Confirmed working

Verified end to end against the running server on 17 Aug.

- **Auth** — OTP request / verify / refresh / logout / me. Test code `0000`.
- **Catalogue** — `GET /vehicles`, `/vehicles/{id}`, `/vehicle-types`. These are
  **public**, so browsing works signed-out.
- **Provider vehicles** — list, create, update, delete, availability toggle.
  (Create is blocked in practice by the P0 image rule above.)

Notes worth keeping for whoever tests next:

- The OTP code is **single-use** — each verify needs a fresh
  `POST /auth/otp/request`, or the second attempt returns no token.
- The `role` sent on the OTP **request** sets `active_role` on the token.
  Requesting with `PROVIDER` yields a provider session.
- Test account `9876543210` is **Vasanth**, `roles: ["PROVIDER","RENTER"]`.
- Access token 900s, refresh 30 days.

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
