"""Registration service for creating a user and their first organization."""

from __future__ import annotations

from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ROLE_OWNER, Membership, Organization, User
from app.security import hash_password

EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


class RegistrationError(Exception):
    """Safe, user-facing registration failure."""


class InvalidEmailError(RegistrationError):
    """The supplied email address is invalid."""


class DuplicateEmailError(RegistrationError):
    """An account already uses the normalized email address."""


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    organization: Organization
    membership: Membership


def normalize_email(email: str) -> str:
    """Validate an email and normalize it to the database's lowercase form."""
    if not isinstance(email, str):
        raise TypeError("email must be a string")
    try:
        validated = validate_email(email.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise InvalidEmailError("email address is invalid") from None
    normalized = validated.normalized.strip().lower()
    if not normalized or len(normalized) > 254:
        raise InvalidEmailError("email address is invalid")
    return normalized


def _is_duplicate_email_error(error: IntegrityError) -> bool:
    """Recognize the named email uniqueness constraint without exposing DB details."""
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == EMAIL_UNIQUE_CONSTRAINT


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    organization_name: str,
) -> RegistrationResult:
    """Create a user, organization, and owner membership atomically.

    The caller supplies a fresh request-scoped Session. This service owns its
    explicit transaction and maps database integrity details to safe errors.
    """
    normalized_email = normalize_email(email)
    if not isinstance(organization_name, str):
        raise TypeError("organization name must be a string")
    normalized_organization_name = organization_name.strip()
    if not normalized_organization_name or len(normalized_organization_name) > 200:
        raise RegistrationError("organization name is invalid")

    password_hash = hash_password(password)
    user = User(email=normalized_email, password_hash=password_hash)
    organization = Organization(name=normalized_organization_name)
    membership = Membership(
        user=user,
        organization=organization,
        role=ROLE_OWNER,
    )

    try:
        with session.begin():
            session.add(user)
            session.add(organization)
            session.flush()
            session.add(membership)
            session.flush()
    except IntegrityError as error:
        if _is_duplicate_email_error(error):
            raise DuplicateEmailError("email address is already registered") from None
        raise RegistrationError("registration could not be completed") from None

    return RegistrationResult(
        user=user,
        organization=organization,
        membership=membership,
    )
