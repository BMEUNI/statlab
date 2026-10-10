"""Password hashing, session tokens and sign-in throttling (chunk 71). Stdlib only."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 3
_MAXMEM = 64 * 1024 * 1024
_KEY_BYTES = 32
SESSION_SECONDS = 14 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 900.0


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=_MAXMEM,
        dklen=_KEY_BYTES,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
        key = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
    except ValueError, binascii.Error:
        return False
    return hmac.compare_digest(key, expected)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_digest(token)


# ponytail: per-process memory; emails never retried keep their entries until restart. Move to
# SQL if this runs multi-process.
class LoginThrottle:
    """Failed sign-ins per email, in memory."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = self._clock()
        recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return len(recent) >= MAX_FAILURES

    def fail(self, key: str) -> None:
        self._failures.setdefault(key, []).append(self._clock())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)
