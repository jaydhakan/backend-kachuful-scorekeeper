"""The only module that reads environment variables. Validated once, at import."""

import os
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import __version__

# Real environment variables win over the local .env file.
load_dotenv(override=False)

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseModel):
    """Typed settings; attribute names are the environment variable names."""

    model_config = ConfigDict(frozen=True)

    APP_ENV: Environment = "local"
    DATABASE_URL: str = Field(..., description="postgresql+asyncpg://user:pass@host:port/db")
    APP_ORIGIN: str = "http://localhost:5173"
    APP_TZ: str = "Asia/Kolkata"
    APP_VERSION: str = __version__
    SEED_ADMIN_EMAIL: str = "jaydhakan11@gmail.com"
    SESSION_DAYS: int = Field(default=180, ge=1)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    WEB_CONCURRENCY: int = Field(default=2, ge=1)
    DB_POOL_SIZE: int = Field(default=10, ge=1)
    API_STATEMENT_TIMEOUT_MS: int = 2000
    JOB_STATEMENT_TIMEOUT_MS: int = 60000
    OG_CACHE_DIR: str = "var/og-cache"
    BACKUP_DIR: str = "var/backups"
    BACKUP_REMOTE: str = ""

    @field_validator("DATABASE_URL")
    @classmethod
    def _asyncpg_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("must start with postgresql+asyncpg://")
        return value

    @field_validator("APP_TZ")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("APP_ORIGIN")
    @classmethod
    def _bare_origin(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")) or value.rstrip("/") != value:
            raise ValueError("must be scheme://host[:port] with no trailing slash")
        return value

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies everywhere except plain-http local and test runs."""
        return self.APP_ENV in ("staging", "production")

    @property
    def tz(self) -> ZoneInfo:
        """APP_TZ as a ZoneInfo."""
        return ZoneInfo(self.APP_TZ)


def _load() -> Settings:
    names = Settings.model_fields.keys()
    raw = {name: os.environ[name] for name in names if os.environ.get(name)}
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid settings:\n{exc}") from exc


settings = _load()
