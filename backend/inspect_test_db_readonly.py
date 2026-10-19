"""Read-only pre-migration inspection of permission_aware_rag_test.

Uses verified test-database safety checks (tests.db_safety) to guarantee:
- Only connects to permission_aware_rag_test.
- Never connects to or touches permission_aware_rag.
- Enforces an explicit PostgreSQL READ ONLY transaction.
- Only executes read-only catalog SELECT queries.
- Uses static queries and parameterized statements.
- Never retrieves or prints user records, credentials, or database URLs.
- Automatically disposes of the engine in a finally block.
"""

import json
from typing import Any

from sqlalchemy import Column, MetaData, String, Table, func, select, text

from tests.db_safety import (
    EXPECTED_TEST_DB,
    build_verified_engine,
    read_database_urls,
)

# Immutable allowlist of tables relevant to M0.6
ALLOWED_TARGET_TABLES = frozenset({"organizations", "users", "memberships", "sessions"})


def _public_table(table_name: str, *columns: Column) -> Table:
    """Build a SQLAlchemy table clause for a public-schema identifier."""
    if table_name not in ALLOWED_TARGET_TABLES and table_name != "alembic_version":
        raise ValueError("Table is not approved for read-only inspection")
    return Table(table_name, MetaData(), *columns, schema="public")


def _count_query(table_name: str):
    table = _public_table(table_name)
    return select(func.count()).select_from(table)


def inspect_test_database() -> dict[str, Any]:
    dev_url, test_url = read_database_urls()
    engine = build_verified_engine(dev_url, test_url)

    try:
        with engine.connect() as conn:
            # Enforce an explicit READ ONLY transaction at the database level
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))

                # 1. Verify connected database name before performing any inspection
                connected_db = conn.execute(text("SELECT current_database()")).scalar_one()
                if connected_db != EXPECTED_TEST_DB:
                    raise RuntimeError(
                        f"Safety check failed: connected to '{connected_db}', expected '{EXPECTED_TEST_DB}'"
                    )

                # 2. Inspect user schemas
                schemas = [
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT schema_name FROM information_schema.schemata "
                            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema') "
                            "AND schema_name NOT LIKE 'pg_toast%' "
                            "AND schema_name NOT LIKE 'pg_temp%' "
                            "ORDER BY schema_name"
                        )
                    )
                ]

                # 3. Inspect public base tables
                public_tables = [
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                            "ORDER BY table_name"
                        )
                    )
                ]

                # 4. Check alembic_version table and all revision rows
                alembic_version_exists = "alembic_version" in public_tables
                alembic_revisions: list[str] = []
                if alembic_version_exists:
                    alembic_version = _public_table(
                        "alembic_version", Column("version_num", String)
                    )
                    alembic_revisions = [
                        row[0]
                        for row in conn.execute(
                            select(alembic_version.c.version_num)
                        )
                    ]
                is_revision_0001 = (
                    alembic_version_exists
                    and alembic_revisions == ["0001"]
                )

                # 5. Inspect target tables and verify row counts
                overlap_details: dict[str, dict[str, Any]] = {}
                target_table_counts: dict[str, int] = {}
                for table_name in sorted(ALLOWED_TARGET_TABLES):
                    if table_name in public_tables:
                        # Row count using static query - never select or print table rows
                        count_query = _count_query(table_name)
                        row_count = conn.execute(count_query).scalar_one()
                        target_table_counts[table_name] = row_count

                        # Constraints via parameterized query
                        constraints = [
                            {
                                "name": row[0],
                                "type": row[1],
                            }
                            for row in conn.execute(
                                text(
                                    "SELECT constraint_name, constraint_type "
                                    "FROM information_schema.table_constraints "
                                    "WHERE table_schema = 'public' AND table_name = :table_name "
                                    "ORDER BY constraint_name"
                                ),
                                {"table_name": table_name},
                            )
                        ]

                        # Indexes via parameterized query
                        indexes = [
                            {
                                "name": row[0],
                                "def": row[1],
                            }
                            for row in conn.execute(
                                text(
                                    "SELECT indexname, indexdef FROM pg_indexes "
                                    "WHERE schemaname = 'public' AND tablename = :table_name "
                                    "ORDER BY indexname"
                                ),
                                {"table_name": table_name},
                            )
                        ]

                        overlap_details[table_name] = {
                            "row_count": row_count,
                            "constraints": constraints,
                            "indexes": indexes,
                        }

                # Derive whether every target table exists and has zero rows
                all_target_tables_exist = ALLOWED_TARGET_TABLES.issubset(set(public_tables))
                all_target_tables_exist_and_empty = (
                    all_target_tables_exist
                    and all(target_table_counts.get(tbl) == 0 for tbl in ALLOWED_TARGET_TABLES)
                )

                return {
                    "connected_database": connected_db,
                    "transaction_mode": "READ ONLY",
                    "schemas": schemas,
                    "public_tables": public_tables,
                    "alembic_version_table_exists": alembic_version_exists,
                    "alembic_revisions": alembic_revisions,
                    "is_revision_0001": is_revision_0001,
                    "target_table_counts": target_table_counts,
                    "all_target_tables_exist_and_empty": all_target_tables_exist_and_empty,
                    "overlapping_tables_found": list(overlap_details.keys()),
                    "overlap_details": overlap_details,
                }
    finally:
        engine.dispose()


def main() -> None:
    results = inspect_test_database()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
