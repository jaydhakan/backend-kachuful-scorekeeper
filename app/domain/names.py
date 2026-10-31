"""Player names: the uniqueness key and the length rule (app: src/domain/names.ts)."""

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
