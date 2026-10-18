"""Optional smoke test: confirms the verified test engine reaches the right database.

Skipped unless pytest is started with --run-db. Our own code runs only
SELECT current_database(). (SQLAlchemy's dialect also issues its own read-only
start-up queries, e.g. version lookups, on the first connection.)
Nothing is created, changed, migrated or deleted.
"""

import pytest
from sqlalchemy import text

from tests.db_safety import EXPECTED_TEST_DB


@pytest.mark.db
def test_verified_engine_is_connected_to_the_test_database(verified_test_engine):
    with verified_test_engine.connect() as connection:
        name = connection.execute(text("SELECT current_database()")).scalar_one()
    assert name == EXPECTED_TEST_DB