"""SQLAlchemy declarative base.

Every model inherits from Base. Alembic's env.py will read Base.metadata to
compare models against the migration history.
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit constraint/index naming. Without this, PostgreSQL invents names, and
# Alembic migrations that later alter or drop a constraint can't refer to it
# reliably. With it, names are deterministic, e.g.:
#   uq_users_email, fk_memberships_user_id_users, ix_sessions_expires_at
# Note: "ck" (check constraints) relies on each CheckConstraint being given a
# name; the naming convention then prefixes it with the table name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Any attribute annotated as `Mapped[datetime]` becomes TIMESTAMP WITH TIME
    # ZONE. This makes timezone-aware timestamps the default for every model,
    # so a naive datetime can't sneak into the schema by accident.
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }