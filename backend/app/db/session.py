"""Database engine and session management.

Nothing here connects to PostgreSQL at import time. The engine is built the
first time get_engine() is called, and SQLAlchemy only opens a connection when
a query is actually executed.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def create_db_engine(database_url: str) -> Engine:
    """Build an engine for an explicit URL.

    The URL is a parameter on purpose: the test suite calls this with the
    *verified* test URL, and the app calls it (via get_engine) with DATABASE_URL.
    Neither path can silently fall back to the other.
    """
    return create_engine(
        database_url,
        # echo=True would log every SQL statement *with parameters*, which
        # include password hashes and session-token hashes. Keep it off.
        echo=False,
        # Keep bound parameter values out of SQLAlchemy error messages.
        hide_parameters=True,
        # Detect connections that PostgreSQL has closed (e.g. after a restart).
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        # No automatic flushing: writes reach the database only when the code
        # says so (session.flush() / session.commit()), which keeps the
        # registration transaction easy to reason about.
        autoflush=False,
        # Objects stay readable after commit (e.g. to build a response) without
        # triggering a surprise re-query.
        expire_on_commit=False,
    )


@lru_cache
def get_engine() -> Engine:
    """The application engine, created lazily from DATABASE_URL."""
    return create_db_engine(get_settings().database_url.get_secret_value())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_engine())


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one Session per request.

    Transaction policy: nothing is committed implicitly. Service functions call
    session.commit() when a unit of work succeeds. If the request fails with an
    exception we roll back, and closing the session always releases the
    connection (closing also discards any uncommitted work).
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()