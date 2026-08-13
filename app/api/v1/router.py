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

api_router = APIRouter()

# Feature routers get mounted here as they are built:
#
# from app.modules.auth.router import router as auth_router
# api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
#
# Next up: Phase 1 — authentication.
