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

__all__ = ["Base"]

# Feature models get imported here as they are built, e.g.
#
#     from app.modules.users.models import User
#     from app.modules.providers.models import ProviderProfile
#
# and added to __all__ below so linters see them as intentionally re-exported
# rather than unused imports.
#
# Next up: Phase 1 — the User model.
