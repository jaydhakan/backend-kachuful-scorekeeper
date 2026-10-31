"""Player names and emails: uniqueness keys and shape rules (app: src/domain/names.ts)."""

import re

NAME_MAX = 14
_SPACES = re.compile(r"\s+")


def tidy_spaces(name: str) -> str:
    """Trimmed, with inner whitespace collapsed to single spaces."""
    return _SPACES.sub(" ", name.strip())


def name_key(name: str) -> str:
    """Names are unique by this key: trimmed, inner spaces collapsed, case ignored."""
    return tidy_spaces(name).lower()


def is_valid_display_name(name: str) -> bool:
    """1-14 characters once tidied (the same rule as a name inside a game)."""
    return 1 <= len(tidy_spaces(name)) <= NAME_MAX


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254


def clean_email(raw: str) -> str | None:
    """Trimmed and lower-cased, or None when it can't be an email (x@y.z, no spaces)."""
    email = raw.strip().lower()
    return email if len(email) <= EMAIL_MAX and _EMAIL.match(email) else None
