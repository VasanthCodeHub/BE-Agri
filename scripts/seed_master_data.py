"""Seed vehicle master data (manufacturers → models → variants).

Run it after the schema exists and PostgreSQL is up:

    uv run python scripts/seed_master_data.py

Idempotent: re-running never creates duplicates, because every row is looked
up by its unique key (manufacturer name; manufacturer+model name;
model+variant name) before insert.

The data is deliberately modest but realistic — enough for the app's
dropdowns to cascade properly across several manufacturers and vehicle types.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.modules.masters.models import (
    VehicleManufacturer,
    VehicleModel,
    VehicleVariant,
)
from app.modules.vehicles.models import FuelType, VehicleType

#: (manufacturer, [(model, type_code, fuel, power_hp, [(variant, year), ...]), ...])
_SEED: list[tuple[str, list[tuple[str, str, FuelType, int, list[tuple[str, int | None]]]]]] = [
    (
        "Mahindra",
        [
            ("475 DI", "TRACTOR", FuelType.DIESEL, 42, [("Standard", 2018), ("Power Plus", 2021)]),
            ("575 DI", "TRACTOR", FuelType.DIESEL, 47, [("Standard", 2019), ("Power Plus", 2022)]),
            ("585 DI", "TRACTOR", FuelType.DIESEL, 50, [("Standard", 2020), ("Power Plus", 2023)]),
            ("Arjun Novo 605 DI", "TRACTOR", FuelType.DIESEL, 55, [("Standard", 2021)]),
            ("Jivo 365 DI", "TRACTOR", FuelType.DIESEL, 36, [("Standard", 2022)]),
        ],
    ),
    (
        "John Deere",
        [
            ("5050D", "TRACTOR", FuelType.DIESEL, 50, [("Standard", 2019), ("Deluxe", 2022)]),
            ("5310", "TRACTOR", FuelType.DIESEL, 55, [("Standard", 2020)]),
            ("3028 EN", "TRACTOR", FuelType.DIESEL, 28, [("Standard", 2021)]),
        ],
    ),
    (
        "Swaraj",
        [
            ("724 XM", "TRACTOR", FuelType.DIESEL, 46, [("Standard", 2018), ("LX", 2021)]),
            ("855 FE", "TRACTOR", FuelType.DIESEL, 52, [("Standard", 2019)]),
            ("735 FE", "TRACTOR", FuelType.DIESEL, 42, [("Standard", 2020)]),
        ],
    ),
    (
        "Massey Ferguson",
        [
            ("1035 DI", "TRACTOR", FuelType.DIESEL, 45, [("Standard", 2018)]),
            ("241 DI", "TRACTOR", FuelType.DIESEL, 50, [("Standard", 2020), ("Deluxe", 2023)]),
        ],
    ),
    (
        "Sonalika",
        [
            ("DI 750 III", "TRACTOR", FuelType.DIESEL, 55, [("Standard", 2019), ("S", 2022)]),
            ("Sikander DI 745", "TRACTOR", FuelType.DIESEL, 48, [("Standard", 2020)]),
        ],
    ),
    (
        "Kubota",
        [
            ("MU5501", "TRACTOR", FuelType.DIESEL, 55, [("Standard", 2021)]),
            ("L4508", "TRACTOR", FuelType.DIESEL, 45, [("Standard", 2019)]),
        ],
    ),
    (
        "TAFE",
        [
            ("MF 241 DI", "TRACTOR", FuelType.DIESEL, 50, [("Standard", 2019)]),
            ("744 GT", "TRACTOR", FuelType.DIESEL, 48, [("Standard", 2020)]),
        ],
    ),
    (
        "VST Shakti",
        [
            ("Shakti 130 DI", "POWER_TILLER", FuelType.DIESEL, 13, [("Standard", 2019)]),
            ("Shakti 135 DI", "POWER_TILLER", FuelType.DIESEL, 13, [("Standard", 2021)]),
        ],
    ),
    (
        "Greaves Cotton",
        [
            ("Tiller Maxx 8HP", "POWER_TILLER", FuelType.DIESEL, 8, [("Standard", 2020)]),
        ],
    ),
    (
        "John Deere",
        [
            ("W80", "HARVESTER", FuelType.DIESEL, 80, [("Standard", 2020)]),
            ("W100", "HARVESTER", FuelType.DIESEL, 100, [("Standard", 2022)]),
        ],
    ),
    (
        "New Holland",
        [
            ("TC5.20", "HARVESTER", FuelType.DIESEL, 120, [("Standard", 2021)]),
        ],
    ),
    (
        "Aspee",
        [
            ("Rocket 600", "SPRAYER", FuelType.PETROL, 6, [("Standard", 2020)]),
            ("Avenger 500", "SPRAYER", FuelType.PETROL, 5, [("Standard", 2022)]),
        ],
    ),
    (
        "JCB",
        [
            (
                "3DX",
                "BACKHOE_LOADER",
                FuelType.DIESEL,
                68,
                [("3DX Standard", 2019), ("3DX Super", 2021)],
            ),
        ],
    ),
]


async def _get_type_id(db: AsyncSession, code: str) -> uuid.UUID | None:
    result = await db.execute(select(VehicleType.id).where(VehicleType.code == code))
    return result.scalar_one_or_none()


async def main() -> None:
    factory = get_session_factory()
    async with factory() as db:
        inserted_manufacturers = 0
        inserted_models = 0
        inserted_variants = 0

        for manufacturer_name, models in _SEED:
            manufacturer = (
                await db.execute(
                    select(VehicleManufacturer).where(VehicleManufacturer.name == manufacturer_name)
                )
            ).scalar_one_or_none()
            if manufacturer is None:
                manufacturer = VehicleManufacturer(name=manufacturer_name)
                db.add(manufacturer)
                await db.flush()
                inserted_manufacturers += 1

            for model_name, type_code, fuel, power_hp, variants in models:
                type_id = await _get_type_id(db, type_code)
                if type_id is None:
                    print(f"  ! skipping {model_name}: vehicle type {type_code} not seeded")
                    continue

                model = (
                    await db.execute(
                        select(VehicleModel).where(
                            VehicleModel.manufacturer_id == manufacturer.id,
                            VehicleModel.name == model_name,
                        )
                    )
                ).scalar_one_or_none()
                if model is None:
                    model = VehicleModel(
                        manufacturer_id=manufacturer.id,
                        name=model_name,
                        vehicle_type_id=type_id,
                        fuel_type=fuel,
                        power_hp=power_hp,
                    )
                    db.add(model)
                    await db.flush()
                    inserted_models += 1

                for variant_name, year in variants:
                    variant = (
                        await db.execute(
                            select(VehicleVariant).where(
                                VehicleVariant.model_id == model.id,
                                VehicleVariant.name == variant_name,
                            )
                        )
                    ).scalar_one_or_none()
                    if variant is None:
                        db.add(
                            VehicleVariant(
                                model_id=model.id,
                                name=variant_name,
                                manufacture_year=year,
                            )
                        )
                        inserted_variants += 1

        await db.commit()

    print(
        f"Seeded master data: {inserted_manufacturers} manufacturers, "
        f"{inserted_models} models, {inserted_variants} variants. "
        "Re-running is safe."
    )


if __name__ == "__main__":
    asyncio.run(main())
