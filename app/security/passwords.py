"""argon2id password hashing and the password rule."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_MIN = 3
PASSWORD_MAX = 128
_WHITESPACE = re.compile(r"\s")

_hasher = PasswordHasher()
# Verified against when the email is unknown, so both failures take the same time.
_DUMMY_HASH = _hasher.hash("kachuful-timing-equaliser")


def is_valid_password(password: str) -> bool:
    """At least 3 and at most 128 characters, no whitespace anywhere. Nothing else."""
    return PASSWORD_MIN <= len(password) <= PASSWORD_MAX and not _WHITESPACE.search(password)


def hash_password(password: str) -> str:
    """argon2id hash with the library defaults."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Whether the password matches; a missing hash still costs one verification."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the hash was made with older parameters."""
    return _hasher.check_needs_rehash(password_hash)
