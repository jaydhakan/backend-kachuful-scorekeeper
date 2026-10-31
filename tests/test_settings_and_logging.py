import json
import logging

import pytest
from pydantic import ValidationError

from app.logger import JsonFormatter, redact
from app.settings import Settings

_URL = "postgresql+asyncpg://u:p@localhost/db"


def test_settings_reject_a_non_asyncpg_url() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="postgresql://u:p@localhost/db")


def test_settings_reject_an_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL=_URL, APP_TZ="Mars/Olympus")


def test_settings_reject_an_origin_with_a_trailing_slash() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL=_URL, APP_ORIGIN="https://play.vectanix.com/")


def test_cookies_are_secure_only_on_staging_and_production() -> None:
    assert Settings(DATABASE_URL=_URL, APP_ENV="production").cookie_secure
    assert Settings(DATABASE_URL=_URL, APP_ENV="staging").cookie_secure
    assert not Settings(DATABASE_URL=_URL, APP_ENV="local").cookie_secure


def test_redact_masks_secrets_at_any_depth() -> None:
    data = {"email": "a@b.c", "nested": [{"password": "pw", "sessionToken": "t"}], "Cookie": "x"}

    assert redact(data) == {
        "email": "a@b.c",
        "nested": [{"password": "[redacted]", "sessionToken": "[redacted]"}],
        "Cookie": "[redacted]",
    }


def test_json_formatter_writes_one_object_with_extras_redacted() -> None:
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hello %s", ("you",), None)
    record.route = "/api/games/{game_id}"
    record.token = "abc"

    line = json.loads(JsonFormatter().format(record))

    assert line["msg"] == "hello you"
    assert line["level"] == "info"
    assert line["route"] == "/api/games/{game_id}"
    assert line["token"] == "[redacted]"
