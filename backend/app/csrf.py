"""Stateless signed double-submit CSRF token utilities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Iterable
from datetime import timedelta
from urllib.parse import urlsplit

_TOKEN_VERSION = "v1"
_SIGNING_PREFIX = b"permission-aware-rag:csrf:v1\x00"
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SIGNATURE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_EXPIRY_PATTERN = re.compile(r"^[0-9]{1,12}$")


def _signature(secret: str, expires: str, nonce: str) -> str:
    message = _SIGNING_PREFIX + expires.encode("ascii") + b"\x00" + nonce.encode("ascii")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_csrf_token(
    secret: str,
    lifetime: timedelta,
    *,
    now: float | None = None,
) -> str:
    """Generate a signed token with an absolute Unix expiry and random nonce."""
    if not isinstance(secret, str) or not secret:
        raise ValueError("CSRF signing configuration is invalid")
    if not isinstance(lifetime, timedelta) or lifetime.total_seconds() <= 0:
        raise ValueError("CSRF token lifetime is invalid")
    try:
        issued_from = time.time() if now is None else now
        expires = str(int(issued_from + lifetime.total_seconds()))
        nonce = secrets.token_urlsafe(32)
        signature = _signature(secret, expires, nonce)
    except (UnicodeError, TypeError, ValueError, OverflowError):
        raise ValueError("CSRF token generation failed") from None
    return f"{_TOKEN_VERSION}.{expires}.{nonce}.{signature}"


def validate_csrf_token(
    token: str | None,
    secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Return false for missing, malformed, expired, or invalidly signed tokens."""
    if (
        not isinstance(token, str)
        or len(token) > 103
        or not isinstance(secret, str)
        or not secret
    ):
        return False
    try:
        version, expires, nonce, supplied_signature = token.split(".")
        if (
            version != _TOKEN_VERSION
            or not _EXPIRY_PATTERN.fullmatch(expires)
            or not _NONCE_PATTERN.fullmatch(nonce)
            or not _SIGNATURE_PATTERN.fullmatch(supplied_signature)
        ):
            return False
        expected_signature = _signature(secret, expires, nonce)
        if not hmac.compare_digest(expected_signature, supplied_signature):
            return False
        current_time = time.time() if now is None else now
        return int(expires) > current_time
    except (UnicodeError, TypeError, ValueError, OverflowError):
        return False


def validate_double_submit(
    cookie_token: str | None,
    header_token: str | None,
    secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Require a matching cookie/header token whose server signature is valid."""
    if not isinstance(cookie_token, str) or not isinstance(header_token, str):
        return False
    try:
        if not hmac.compare_digest(cookie_token, header_token):
            return False
    except (TypeError, UnicodeError):
        return False
    return validate_csrf_token(cookie_token, secret, now=now)


def is_allowed_origin(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    """Use exact allowlist membership; absent or similar-looking origins fail."""
    return isinstance(origin, str) and bool(origin) and origin in allowed_origins


def _raw_header_values(scope: dict, header_name: bytes) -> list[bytes] | None:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, (list, tuple)):
        return None
    return [
        value
        for name, value in raw_headers
        if isinstance(name, bytes) and name.lower() == header_name.lower()
    ]


def get_single_raw_header(scope: dict, header_name: bytes) -> tuple[str | None, bool]:
    """Read exactly one well-formed header from ASGI's raw header list."""
    values = _raw_header_values(scope, header_name)
    if values is None or len(values) != 1 or not isinstance(values[0], bytes):
        return None, False
    try:
        value = values[0].decode("ascii").strip(" \t")
    except UnicodeDecodeError:
        return None, False
    if not value or any(ord(char) < 0x20 or ord(char) == 0x7f for char in value):
        return None, False
    return value, True


def _host_matches_allowed_authority(
    host_header: str, request_scheme: str, allowed_origins: Iterable[str]
) -> bool:
    """Match Host and request scheme against one configured origin."""
    if (
        host_header.endswith(":")
        or any(char.isspace() for char in host_header)
        or any(char in host_header for char in "/\\?#,@")
    ):
        return False
    try:
        supplied = urlsplit(f"//{host_header}")
        if (
            supplied.netloc != host_header
            or supplied.hostname is None
            or (supplied.port is not None and not 1 <= supplied.port <= 65535)
        ):
            return False
        supplied_host = supplied.hostname.casefold()
        supplied_port = supplied.port
        for origin in allowed_origins:
            configured = urlsplit(origin)
            if (
                configured.scheme != request_scheme
                or configured.scheme not in ("http", "https")
                or not configured.hostname
            ):
                continue
            configured_port = configured.port
            default_port = 443 if request_scheme == "https" else 80
            effective_supplied_port = (
                default_port if supplied_port is None else supplied_port
            )
            effective_configured_port = (
                default_port if configured_port is None else configured_port
            )
            if (
                supplied_host == configured.hostname.casefold()
                and effective_supplied_port == effective_configured_port
            ):
                return True
    except (TypeError, ValueError):
        return False
    return False


def is_valid_bootstrap_origin(scope: dict, allowed_origins: Iterable[str]) -> bool:
    """Validate an allowlisted Origin or a browser same-origin fallback.

    The fallback relies on browser-controlled Fetch Metadata and Host. A Host
    match is meaningful only when the deployment proxy routes a canonical Host
    to this app; this is not client authentication, and forwarded headers are
    deliberately ignored.
    """
    origin_values = _raw_header_values(scope, b"origin")
    if origin_values is None:
        return False
    if origin_values:
        origin, valid = get_single_raw_header(scope, b"origin")
        return valid and is_allowed_origin(origin, allowed_origins)

    fetch_site, fetch_site_ok = get_single_raw_header(scope, b"sec-fetch-site")
    host, host_ok = get_single_raw_header(scope, b"host")
    request_scheme = scope.get("scheme")
    # ASGI supplies the effective transport scheme. Do not infer it from
    # client-controlled forwarding headers; an unknown/proxy-inconsistent
    # scheme fails closed instead of matching an origin by authority alone.
    if request_scheme not in ("http", "https"):
        return False
    return (
        fetch_site_ok
        and fetch_site is not None
        and fetch_site.casefold() == "same-origin"
        and host_ok
        and host is not None
        and _host_matches_allowed_authority(host, request_scheme, allowed_origins)
    )


def _split_cookie_header(value: str) -> list[str] | None:
    """Split cookie pairs without treating quoted semicolons as separators."""
    pairs: list[str] = []
    start = 0
    in_quotes = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif in_quotes and char == "\\":
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
        elif char == ";" and not in_quotes:
            pairs.append(value[start:index])
            start = index + 1
    if in_quotes or escaped:
        return None
    pairs.append(value[start:])
    return pairs


def get_unique_raw_cookie(scope: dict, cookie_name: str) -> tuple[str | None, bool]:
    """Find exactly one named cookie across all raw Cookie headers."""
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, (list, tuple)):
        return None, False
    cookie_headers = [
        value
        for name, value in raw_headers
        if isinstance(name, bytes) and name.lower() == b"cookie"
    ]
    if not cookie_headers:
        return None, False

    found_values: list[str] = []
    for raw_cookie in cookie_headers:
        if not isinstance(raw_cookie, bytes):
            return None, False
        # Cookie header bytes use a byte-preserving mapping; token validation
        # below accepts only the ASCII token alphabet.
        pairs = _split_cookie_header(raw_cookie.decode("latin-1"))
        if pairs is None:
            return None, False
        for pair in pairs:
            item = pair.strip(" \t")
            if not item:
                continue
            name, separator, value = item.partition("=")
            if name.strip(" \t") == cookie_name:
                if not separator:
                    return None, False
                found_values.append(value.strip(" \t"))

    if len(found_values) != 1:
        return None, False
    return found_values[0], True
