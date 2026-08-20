"""Fill `vehicle_types.name_ta` using the free MyMemory translation API.

Run it after the schema exists (migrations applied) and PostgreSQL is up:

    uv run python scripts/translate_vehicle_types.py

It finds vehicle types whose `name_ta` is still empty, translates `name_en` to
Tamil, writes `name_ta`, and prints the resulting mapping. Types that already
have a Tamil name are left alone, so re-running is safe.

Free APIs return garbage for some words — review the printed translations
before trusting them (the seeded list in the 20260814 migration was reviewed
by hand).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import get_engine, get_session_factory
from app.integrations.translation import translate_to_tamil
from app.modules.vehicles.models import VehicleType

log = get_logger(__name__)

#: Mirrors the seed in the 20260814 migration — used only when the database is
#: down so the script still shows what the translations will be.
_FALLBACK = [
    ("TRACTOR", "Tractor"),
    ("POWER_TILLER", "Power tiller"),
    ("HARVESTER", "Harvester"),
    ("ROTAVATOR", "Rotavator"),
    ("PLOUGH", "Plough"),
    ("SEED_DRILL", "Seed drill"),
    ("SPRAYER", "Sprayer"),
    ("THRESHER", "Thresher"),
    ("BALER", "Baler"),
    ("LEVELLER", "Land leveller"),
    ("TRAILER", "Trailer"),
    ("WATER_TANKER", "Water tanker"),
]


async def main() -> None:
    rows = await _load_types()
    if not rows:
        print("No vehicle types in the database — using the seed fallback list.")
        rows = _FALLBACK

    print(f"Translating {len(rows)} vehicle type names to Tamil...\n")
    updated: list[tuple[str, str, str]] = []
    for code, name_en in rows:
        tamil = await translate_to_tamil(name_en)
        updated.append((code, name_en, tamil))
        print(f"  {code:<14} {name_en:<16} -> {tamil}")

    if rows is _FALLBACK:
        return
    await _save(updated)


async def _load_types() -> list[tuple[str, str]]:
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(VehicleType.code, VehicleType.name_en).where(
                    VehicleType.name_ta.is_(None) | (VehicleType.name_ta == "")
                )
            )
            return list(result.all())
    except Exception as exc:
        log.warning("db_unavailable_for_translation", error=str(exc))
        return []


async def _save(updated: list[tuple[str, str, str]]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for code, _name_en, tamil in updated:
            row = await session.scalar(select(VehicleType).where(VehicleType.code == code))
            if row is not None:
                row.name_ta = tamil
        await session.commit()
    print(f"\nSaved {len(updated)} Tamil names to the database.")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
