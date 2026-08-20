"""Favourite schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.modules.vehicles.schemas import VehicleCardOut


class FavouriteIn(BaseModel):
    vehicle_id: uuid.UUID


class FavouriteOut(BaseModel):
    id: uuid.UUID
    vehicle: VehicleCardOut
    created_at: str

    model_config = {"from_attributes": True}
