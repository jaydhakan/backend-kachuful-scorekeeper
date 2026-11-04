"""The app's themes and the appearance everyone sees until an admin changes it."""

from enum import StrEnum


class Theme(StrEnum):
    """A look the app ships with (the app's theme ids)."""

    SUNSET_GLASS = "sunset-glass"
    YERBA_SUN = "yerba-sun"
    EDITORIAL_PAPER = "editorial-paper"
    EMERALD_GILT = "emerald-gilt"


DEFAULT_THEME = Theme.SUNSET_GLASS

THEME_KEY = "theme.default"
ALLOW_OVERRIDE_KEY = "theme.allow_user_override"

# Setting key → value before anyone has changed it.
APPEARANCE_DEFAULTS: dict[str, str | bool] = {
    THEME_KEY: DEFAULT_THEME.value,
    ALLOW_OVERRIDE_KEY: False,
}


def known_theme(value: object) -> Theme | None:
    """The theme a stored id names, or None when this build doesn't ship it.

    A stored id can outlive its theme (a rollback after someone picked a newer one).
    """
    return next((theme for theme in Theme if theme.value == value), None)
