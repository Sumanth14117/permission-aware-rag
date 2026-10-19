"""Password hashing and opaque session-token primitives."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_password_hasher = PasswordHasher()
_SESSION_TOKEN_BYTES = 32
MAX_PASSWORD_BYTES = 1024


def is_valid_password_input(password: object) -> bool:
    """Return whether password is non-empty valid Unicode within the byte limit."""
    if not isinstance(password, str) or not password:
        return False
    if len(password) > MAX_PASSWORD_BYTES:
        return False
    try:
        return len(password.encode("utf-8")) <= MAX_PASSWORD_BYTES
    except UnicodeEncodeError:
        return False


def hash_password(password: str) -> str:
    """Return an Argon2id hash for a non-empty password within the input limit."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if not is_valid_password_input(password):
        raise ValueError("password must be valid Unicode and at most 1024 UTF-8 bytes")
    return _password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Return whether password matches encoded_hash; malformed inputs fail closed."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if not isinstance(encoded_hash, str):
        raise TypeError("encoded_hash must be a string")
    if not is_valid_password_input(password) or not encoded_hash:
        return False
    try:
        encoded_hash.encode("ascii")
        return _password_hasher.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, UnicodeError):
        return False


def generate_session_token() -> str:
    """Return a URL-safe, cryptographically random session token."""
    return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """Return the lowercase SHA-256 hex digest of a non-empty token."""
    if not isinstance(token, str):
        raise TypeError("token must be a string")
    if not token:
        raise ValueError("token must not be empty")
    try:
        token_bytes = token.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("token must contain valid Unicode text") from None
    return hashlib.sha256(token_bytes).hexdigest()
