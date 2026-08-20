"""API v1 router — the single place feature modules are mounted.

Each module owns its own router; this file assembles them. Adding a feature
means writing its module and adding one `include_router` line here.

Why the version prefix (/api/v1) from day one: you cannot force users to
update a mobile app. Version 1 of the Flutter app will keep calling v1
endpoints for months after you ship v2. Without a version in the path, the
only way to change a response shape is to break every installed app.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.contact.router import router as contact_router
from app.modules.favourites.router import router as favourites_router
from app.modules.masters.router import router as masters_router
from app.modules.notifications.router import router as notifications_router
from app.modules.profile.router import router as profile_router
from app.modules.provider.router import router as provider_router
from app.modules.reviews.router import router as reviews_router
from app.modules.uploads.router import router as uploads_router
from app.modules.vehicles.router import router as vehicles_router

api_router = APIRouter()

# One line per feature module. The `tags` value is what groups the endpoints
# into a collapsible section in Swagger, so a module maps to a folder AND to a
# section in the docs.
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])

# No prefix: this module owns both provider-only paths (/provider/vehicles) and
# public ones (/vehicles, /vehicle-types), so the prefixes live on the routes.
api_router.include_router(vehicles_router)

# Vehicle master data for the app's dropdowns (manufacturer → model → variant).
api_router.include_router(masters_router)

# Direct contact between a user and a provider — the product has no bookings.
api_router.include_router(contact_router)

# Upload authorisation. Its own module because provider verification documents
# (Phase 5) will need the same signature.
api_router.include_router(uploads_router)

api_router.include_router(favourites_router)
api_router.include_router(reviews_router)
api_router.include_router(notifications_router)
api_router.include_router(profile_router, tags=["profile"])
api_router.include_router(provider_router, tags=["provider"])

# Coming next:
# api_router.include_router(search_router, prefix="/search", tags=["search"])
