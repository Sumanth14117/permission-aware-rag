"""Transaction handoff for Alembic after database identity verification.

SQLAlchemy 2.x autobegins when the identity SELECT runs. Commit that read-only
verification transaction before handing the connection to Alembic, so Alembic
can own and finalize the migration transaction.
"""

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.migration_safety import MigrationSafetyError


def run_migrations_with_engine(
    engine,
    migration_context,
    target_metadata,
    expected_database: str,
) -> None:
    """Run migrations and always close the connection and dispose the engine."""
    try:
        try:
            connection = engine.connect()
        except DBAPIError:
            raise MigrationSafetyError(
                f"Could not connect to the migration target database '{expected_database}'"
            ) from None

        with connection:
            try:
                run_migrations_with_owned_transaction(
                    connection,
                    migration_context,
                    target_metadata,
                    expected_database,
                )
            except DBAPIError:
                raise MigrationSafetyError(
                    "Could not verify database identity or execute the migration target"
                ) from None
    finally:
        engine.dispose()


def run_migrations_with_owned_transaction(
    connection,
    migration_context,
    target_metadata,
    expected_database: str,
) -> None:
    """Verify identity, then let Alembic own the migration transaction."""
    actual_database = connection.execute(text("SELECT current_database()")).scalar_one()
    if actual_database != expected_database:
        raise MigrationSafetyError(
            "Connected database identity does not match the validated migration target"
        )

    # End SQLAlchemy 2.x's autobegun identity-check transaction. Otherwise
    # Alembic sees an externally owned transaction, later rolled back on close.
    connection.commit()

    migration_context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with migration_context.begin_transaction():
        migration_context.run_migrations()
