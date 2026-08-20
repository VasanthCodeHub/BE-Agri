"""Favourite (bookmark) ORM model."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.users.models import User
from app.modules.vehicles.models import Vehicle


class Favourite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "favourites"
    __table_args__ = (
        UniqueConstraint("user_id", "vehicle_id", name="uq_favourites_user_vehicle"),
        Index(
            "ix_favourites_user_created",
            "user_id",
            "created_at",
            postgresql_using="btree",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )

    user: Mapped[User] = relationship(lazy="selectin")
    vehicle: Mapped[Vehicle] = relationship(lazy="selectin")
