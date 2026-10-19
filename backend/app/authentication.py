"""Authentication and persisted session lifecycle services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.models import User, UserSession
from app.registration import InvalidEmailError, normalize_email
from app.security import (
    generate_session_token,
    hash_session_token,
    is_valid_password_input,
    verify_password,
)

# A one-time Argon2id hash of an unrelated random value. Verifying against it
# makes nonexistent-account login attempts perform password-hash work too.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$X7W8jN/94X5TmRagLAFj4w$"
    "zhrgOwKwYMZkQsOq+S0aDFZFP7heDr2j9+16b3iVX6k"
)


class AuthenticationError(Exception):
    """Generic invalid-credentials result safe to return to a caller."""


class AuthenticationServiceError(Exception):
    """Authentication storage operation failed."""


@dataclass(frozen=True)
class LoginResult:
    user: User
    session: UserSession
    raw_token: str


def authenticate_and_create_session(
    db: Session,
    *,
    email: str,
    password: str,
    session_ttl: timedelta,
    now: datetime | None = None,
) -> LoginResult:
    """Validate credentials and persist a session in one transaction."""
    if not is_valid_password_input(password):
        raise AuthenticationError("invalid email or password")

    try:
        normalized_email = normalize_email(email)
    except (InvalidEmailError, TypeError):
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationError("invalid email or password") from None

    try:
        with db.begin():
            user = db.execute(
                select(User).where(User.email == normalized_email)
            ).scalar_one_or_none()
            if user is None:
                verify_password(password, _DUMMY_PASSWORD_HASH)
                raise AuthenticationError("invalid email or password")

            password_matches = verify_password(password, user.password_hash)
            if not user.is_active or not password_matches:
                raise AuthenticationError("invalid email or password")

            issued_at = now or datetime.now(timezone.utc)
            raw_token = generate_session_token()
            persisted_session = UserSession(
                user=user,
                token_hash=hash_session_token(raw_token),
                expires_at=issued_at + session_ttl,
            )
            db.add(persisted_session)
            db.flush()
    except AuthenticationError:
        raise
    except SQLAlchemyError:
        raise AuthenticationServiceError("authentication storage is unavailable") from None

    return LoginResult(user=user, session=persisted_session, raw_token=raw_token)


def is_session_valid(
    persisted_session: UserSession,
    user: User,
    *,
    now: datetime | None = None,
) -> bool:
    """Check expiry, revocation, and account activity for a loaded session."""
    current_time = now or datetime.now(timezone.utc)
    return (
        persisted_session.revoked_at is None
        and persisted_session.expires_at > current_time
        and user.is_active
    )


def get_session_user(
    db: Session,
    raw_token: str | None,
    *,
    now: datetime | None = None,
) -> User | None:
    """Resolve a cookie token only when its session and account remain valid."""
    if not raw_token:
        return None
    try:
        token_hash = hash_session_token(raw_token)
        with db.begin():
            persisted_session = db.execute(
                select(UserSession)
                .options(joinedload(UserSession.user))
                .where(UserSession.token_hash == token_hash)
            ).scalar_one_or_none()
            if persisted_session is None:
                return None
            user = persisted_session.user
            return user if is_session_valid(persisted_session, user, now=now) else None
    except SQLAlchemyError:
        raise AuthenticationServiceError("authentication storage is unavailable") from None
    except (TypeError, ValueError):
        return None


def revoke_session(
    db: Session,
    raw_token: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Revoke a matching persisted session; return False for absent/invalid tokens."""
    if not raw_token:
        return False
    try:
        token_hash = hash_session_token(raw_token)
    except (TypeError, ValueError):
        return False

    try:
        with db.begin():
            persisted_session = db.execute(
                select(UserSession).where(UserSession.token_hash == token_hash)
            ).scalar_one_or_none()
            if persisted_session is None or persisted_session.revoked_at is not None:
                return False
            persisted_session.revoked_at = now or datetime.now(timezone.utc)
            db.flush()
            return True
    except SQLAlchemyError:
        raise AuthenticationServiceError("authentication storage is unavailable") from None
