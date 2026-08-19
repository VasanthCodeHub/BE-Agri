"""Model registry — the one place that imports every ORM model.

Alembic's `--autogenerate` builds migrations by comparing `Base.metadata`
against the real database. But a model only lands on `Base.metadata` if its
module has actually been imported. Any model missing from here is invisible to
Alembic, which means:

  - a new table silently never gets a migration, or worse
  - autogenerate thinks an existing table is unwanted and writes a DROP.

**RULE: when you add a model, add its import here.** It is one line, and it is
the difference between a safe migration and a data-loss migration.
"""

from app.db.base import Base
from app.modules.auth.models import OtpRequest, RefreshToken
from app.modules.favourites.models import Favourite
from app.modules.notifications.models import Notification
from app.modules.reviews.models import Review
from app.modules.users.models import User, UserRoleAssignment
from app.modules.vehicles.models import Vehicle, VehicleImage, VehicleType
from app.modules.bookings.models import Booking  # uncomment when bookings module exists

# Listing them here marks the imports as intentional re-exports, so linters do
# not flag them as unused.
__all__ = [
    "Base",
    "Booking",
    "Favourite",
    "Notification",
    "OtpRequest",
    "RefreshToken",
    "Review",
    "User",
    "UserRoleAssignment",
    "Vehicle",
    "VehicleImage",
    "VehicleType",
]
