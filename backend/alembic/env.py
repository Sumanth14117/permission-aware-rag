"""Alembic environment.

Importing this module does not apply migrations and does not connect to
PostgreSQL. Connection happens only inside ``run_migrations_online``, which
Alembic calls when a command actually runs.

Offline SQL generation still requires ``-x target=dev`` or ``-x target=test``
and validates the *configured* database identity. It does not verify a live
database.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.base import Base
from app.db.migration_safety import (
    MigrationSafetyError,
    resolve_migration_target,
    target_from_x_arguments,
)
from app.db.session import create_db_engine
import app.models  # noqa: F401  — register all tables on Base.metadata

# Redacted placeholder only. Never put a real URL in alembic.ini or here.
_REDACTED_SQLALCHEMY_URL = "postgresql+psycopg://redacted:redacted@redacted/redacted"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", _REDACTED_SQLALCHEMY_URL)

target_metadata = Base.metadata


def _secret(value) -> str | None:
    if value is None:
        return None
    return value.get_secret_value()


def _resolve_from_config():
    target = target_from_x_arguments(context.get_x_argument(as_dictionary=True))
    settings = get_settings()
    return resolve_migration_target(
        target,
        database_url=_secret(settings.database_url),
        test_database_url=_secret(settings.test_database_url),
    )


def run_migrations_offline() -> None:
    """Emit SQL without connecting.

    Still fail-closed on target selection and configured URL identity.
    This does **not** prove that a live PostgreSQL instance exists or matches.
    """
    resolved = _resolve_from_config()
    context.configure(
        url=resolved.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations to the validated target. Not invoked at import time."""
    resolved = _resolve_from_config()
    engine = create_db_engine(resolved.sqlalchemy_url)
    try:
        try:
            connection = engine.connect()
        except DBAPIError:
            raise MigrationSafetyError(
                f"Could not connect to the migration target database '{resolved.database}'"
            ) from None

        with connection:
            try:
                actual_database = connection.execute(text("SELECT current_database()")).scalar_one()
            except DBAPIError:
                raise MigrationSafetyError(
                    "Could not verify database identity for the migration target"
                ) from None

            if actual_database != resolved.database:
                raise MigrationSafetyError(
                    "Connected database identity does not match the validated migration target"
                )
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def run_alembic_env() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


# Alembic CLI sets cmd_opts before exec'ing this file. A plain import does not,
# so importing env.py cannot apply migrations or open a connection.
if getattr(config, "cmd_opts", None) is not None:
    run_alembic_env()
