"""Contact call model — one row per call a user initiated toward a provider."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.modules.users.models import User
from app.modules.vehicles.models import Vehicle


class ContactCall(UUIDPrimaryKeyMixin, Base):
    """A recorded call intent: caller → provider, for one vehicle.

    Kept rather than discarded so the provider dashboard can show how much
    interest their listings get, and so abuse (one caller hammering one
    provider) is visible.
    """

    __tablename__ = "contact_calls"
    __table_args__ = (
        Index(
            "ix_contact_calls_provider_created",
            "provider_user_id",
            "created_at",
            postgresql_using="btree",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    caller_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    caller: Mapped[User] = relationship(foreign_keys=[caller_user_id], lazy="selectin")
    provider: Mapped[User] = relationship(foreign_keys=[provider_user_id], lazy="selectin")
    vehicle: Mapped[Vehicle] = relationship(lazy="selectin")
