"""Database-independent tests for user and organization registration."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ROLE_OWNER, Membership, Organization, User
from app.registration import (
    DuplicateEmailError,
    InvalidEmailError,
    RegistrationError,
    normalize_email,
    register_user,
)
from app.security import verify_password


class _Transaction:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.session.committed = True
        else:
            self.session.rolled_back = True
        return False


class _Session:
    def __init__(self, *, failure_on_flush=None):
        self.failure_on_flush = failure_on_flush
        self.added = []
        self.flush_count = 0
        self.committed = False
        self.rolled_back = False

    def begin(self):
        return _Transaction(self)

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        self.flush_count += 1
        if self.failure_on_flush is not None and self.flush_count == self.failure_on_flush[0]:
            raise self.failure_on_flush[1]


def _integrity_error(constraint_name, detail="sensitive database detail"):
    original = Exception(detail)
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("INSERT", {}, original)


def test_email_is_trimmed_validated_and_lowercased():
    assert normalize_email("  Alice.Example@Example.COM  ") == "alice.example@example.com"


@pytest.mark.parametrize("email", ["", "not-an-email", "user@localhost"])
def test_invalid_email_is_rejected_safely(email):
    with pytest.raises(InvalidEmailError, match="email address is invalid"):
        normalize_email(email)


def test_normalize_email_rejects_non_string_input():
    with pytest.raises(TypeError, match="email must be a string"):
        normalize_email(None)


def test_registration_creates_user_organization_and_owner_membership():
    session = _Session()

    result = register_user(
        session,
        email=" Owner@Example.com ",
        password="correct horse battery staple",
        organization_name=" Example Org ",
    )

    assert isinstance(result.user, User)
    assert result.user.email == "owner@example.com"
    assert result.user.password_hash != "correct horse battery staple"
    assert verify_password("correct horse battery staple", result.user.password_hash)
    assert isinstance(result.organization, Organization)
    assert result.organization.name == "Example Org"
    assert isinstance(result.membership, Membership)
    assert result.membership.user is result.user
    assert result.membership.organization is result.organization
    assert result.membership.role == ROLE_OWNER
    assert session.added == [result.user, result.organization, result.membership]
    assert session.flush_count == 2
    assert session.committed
    assert not session.rolled_back


def test_registration_uses_shared_password_byte_limit_before_transaction():
    session = _Session()
    password = "é" * 513  # 1,026 UTF-8 bytes

    with pytest.raises(ValueError, match="password must be valid Unicode") as error:
        register_user(
            session,
            email="owner@example.com",
            password=password,
            organization_name="Example",
        )

    assert password not in str(error.value)
    assert session.flush_count == 0
    assert not session.added
    assert not session.committed
    assert not session.rolled_back


def test_duplicate_email_integrity_error_rolls_back_and_is_redacted():
    session = _Session(
        failure_on_flush=(
            1,
            _integrity_error("uq_users_email", "duplicate alice@example.com"),
        )
    )

    with pytest.raises(DuplicateEmailError) as error:
        register_user(
            session,
            email="alice@example.com",
            password="sensitive password",
            organization_name="Example",
        )

    assert str(error.value) == "email address is already registered"
    assert "alice@example.com" not in str(error.value)
    assert "sensitive password" not in str(error.value)
    assert session.rolled_back
    assert not session.committed


def test_other_integrity_error_is_safe_and_rolls_back():
    session = _Session(
        failure_on_flush=(
            1,
            _integrity_error("ck_organizations_name_not_blank", "private database detail"),
        )
    )

    with pytest.raises(RegistrationError) as error:
        register_user(
            session,
            email="owner@example.com",
            password="sensitive password",
            organization_name="Example",
        )

    assert str(error.value) == "registration could not be completed"
    assert "private database detail" not in str(error.value)
    assert "sensitive password" not in str(error.value)
    assert session.rolled_back
    assert not session.committed


def test_mid_registration_failure_rolls_back_without_partial_commit():
    session = _Session(failure_on_flush=(2, RuntimeError("controlled failure")))

    with pytest.raises(RuntimeError, match="controlled failure"):
        register_user(
            session,
            email="owner@example.com",
            password="sensitive password",
            organization_name="Example",
        )

    assert len(session.added) == 3
    assert session.rolled_back
    assert not session.committed
