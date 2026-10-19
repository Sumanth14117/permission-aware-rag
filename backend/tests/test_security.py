import hashlib
import re
from types import SimpleNamespace

import pytest
from app.security import (
    MAX_PASSWORD_BYTES,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)


def test_password_hash_verifies_without_containing_plaintext():
    password = "correct horse battery staple"
    encoded = hash_password(password)

    assert password not in encoded
    assert encoded.startswith("$argon2id$")
    assert verify_password(password, encoded)


def test_incorrect_password_is_rejected():
    encoded = hash_password("correct password")

    assert not verify_password("incorrect password", encoded)


def test_correct_and_incorrect_password_verification_remains_supported():
    encoded = hash_password("valid password")

    assert verify_password("valid password", encoded)
    assert not verify_password("different password", encoded)


def test_password_hashes_use_independent_salts():
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first)
    assert verify_password("same password", second)


@pytest.mark.parametrize("password", [None, 123])
def test_password_hash_rejects_non_string_input(password):
    with pytest.raises(TypeError):
        hash_password(password)


def test_password_hash_rejects_empty_input():
    with pytest.raises(ValueError):
        hash_password("")


def test_password_byte_limit_accepts_exact_ascii_boundary():
    password = "a" * MAX_PASSWORD_BYTES
    encoded = hash_password(password)

    assert verify_password(password, encoded)


def test_password_byte_limit_rejects_oversized_input_without_echoing_it():
    password = "s" * (MAX_PASSWORD_BYTES + 1)

    with pytest.raises(ValueError) as error:
        hash_password(password)

    assert str(error.value) == "password must be valid Unicode and at most 1024 UTF-8 bytes"
    assert password not in str(error.value)


def test_password_limit_counts_multibyte_utf8_bytes():
    boundary_password = "é" * (MAX_PASSWORD_BYTES // 2)
    oversized_password = boundary_password + "é"
    encoded = hash_password(boundary_password)

    assert len(boundary_password.encode("utf-8")) == MAX_PASSWORD_BYTES
    assert verify_password(boundary_password, encoded)
    with pytest.raises(ValueError):
        hash_password(oversized_password)


def test_password_verification_fails_before_argon2_for_oversized_input(monkeypatch):
    def unexpected_verify(*args, **kwargs):
        raise AssertionError("Argon2 verify must not run for oversized input")

    monkeypatch.setattr("app.security._password_hasher", SimpleNamespace(verify=unexpected_verify))

    assert not verify_password("x" * (MAX_PASSWORD_BYTES + 1), "$argon2id$placeholder")


def test_password_hash_rejects_unpaired_surrogate_without_echoing_it():
    password = "secret\ud800password"

    with pytest.raises(ValueError) as error:
        hash_password(password)

    assert str(error.value) == "password must be valid Unicode and at most 1024 UTF-8 bytes"
    assert password not in str(error.value)


def test_password_verification_rejects_unpaired_surrogate():
    encoded = hash_password("valid password")

    assert not verify_password("invalid\ud800password", encoded)


def test_password_verification_fails_before_argon2_for_unpaired_surrogate(monkeypatch):
    def unexpected_verify(*args, **kwargs):
        raise AssertionError("Argon2 verify must not run for invalid Unicode")

    monkeypatch.setattr("app.security._password_hasher", SimpleNamespace(verify=unexpected_verify))

    assert not verify_password("invalid\ud800password", "$argon2id$placeholder")


@pytest.mark.parametrize("password", [None, 123])
def test_password_verification_rejects_non_string_password(password):
    with pytest.raises(TypeError):
        verify_password(password, "not-a-valid-hash")


@pytest.mark.parametrize("encoded", [None, 123])
def test_password_verification_rejects_non_string_hash(encoded):
    with pytest.raises(TypeError):
        verify_password("password", encoded)


@pytest.mark.parametrize("encoded", ["", "not-a-hash", "$argon2id$malformed"])
def test_password_verification_fails_closed_for_empty_or_malformed_hash(encoded):
    assert not verify_password("password", encoded)


def test_password_verification_fails_closed_for_non_ascii_encoded_hash():
    assert not verify_password("password", "$argon2id$malformed\ud800")


def test_session_token_generation_is_url_safe_and_random():
    token = generate_session_token()

    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert len(token) >= 43
    assert token != generate_session_token()


def test_session_token_hash_is_sha256_hex():
    token = "opaque-session-token"
    digest = hash_session_token(token)

    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert token not in digest


def test_different_session_tokens_have_different_hashes():
    assert hash_session_token("token-one") != hash_session_token("token-two")


def test_generated_session_token_hashes_with_existing_contract():
    token = generate_session_token()

    assert hash_session_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("token", [None, 123])
def test_session_token_hash_rejects_non_string_input(token):
    with pytest.raises(TypeError):
        hash_session_token(token)


def test_session_token_hash_rejects_empty_input():
    with pytest.raises(ValueError):
        hash_session_token("")


def test_session_token_hash_rejects_unpaired_surrogate_without_echoing_it():
    token = "secret\ud800token"

    with pytest.raises(ValueError) as error:
        hash_session_token(token)

    assert str(error.value) == "token must contain valid Unicode text"
    assert token not in str(error.value)
