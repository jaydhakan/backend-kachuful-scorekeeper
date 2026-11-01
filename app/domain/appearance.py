"""The app's themes and the appearance everyone sees until an admin changes it."""

from enum import StrEnum


class Theme(StrEnum):
    """A look the app ships with (the app's theme ids)."""

    SUNSET_GLASS = "sunset-glass"
    YERBA_SUN = "yerba-sun"
    EDITORIAL_PAPER = "editorial-paper"


THEME_KEY = "theme.default"
ALLOW_OVERRIDE_KEY = "theme.allow_user_override"

# Setting key → value before anyone has changed it.
APPEARANCE_DEFAULTS: dict[str, str | bool] = {
    THEME_KEY: Theme.SUNSET_GLASS.value,
    ALLOW_OVERRIDE_KEY: False,
}
