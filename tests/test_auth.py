"""Password hashing, session tokens and sign-in throttling (chunk 71)."""

from __future__ import annotations

import pytest
from statlab.auth import (
    LoginThrottle,
    hash_password,
    new_session_token,
    token_digest,
    verify_password,
)

PASSWORD_TEXT = "correct horse battery"  # noqa: S105 -- test value, not a real credential


def test_hash_and_verify_round_trip() -> None:
    encoded = hash_password(PASSWORD_TEXT)
    parts = encoded.split("$")
    assert len(parts) == 6
    assert parts[:4] == ["scrypt", "32768", "8", "3"]
    assert verify_password(PASSWORD_TEXT, encoded)
    assert not verify_password("wrong horse battery", encoded)
    assert hash_password(PASSWORD_TEXT) != encoded


@pytest.mark.parametrize(
    "encoded",
    [
        "plain",
        "bcrypt$1$2$3$4$5",
        "scrypt$x$8$1$AAAA$AAAA",
        "scrypt$1$8$1$AAAA$AAAA",
        "scrypt$16$8$1$!!$AAAA",
    ],
)
def test_verify_rejects_malformed_hashes(encoded: str) -> None:
    assert verify_password("x", encoded) is False


def test_session_token_digest() -> None:
    token, digest = new_session_token()
    assert digest == token_digest(token)
    assert len(digest) == 64
    assert token != digest
    assert new_session_token()[0] != token


def test_login_throttle_window() -> None:
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(4):
        throttle.fail("a")
    assert throttle.blocked("a") is False
    throttle.fail("a")
    assert throttle.blocked("a") is True
    assert throttle.blocked("b") is False
    now[0] = 901.0
    assert throttle.blocked("a") is False
    throttle.fail("a")
    assert throttle.blocked("a") is False
    throttle.clear("a")
    throttle.clear("missing")
    assert throttle.blocked("a") is False
