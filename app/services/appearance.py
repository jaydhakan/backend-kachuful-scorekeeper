"""The app-wide appearance: read by everyone, changed by admins, every change audited."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.appearance import ALLOW_OVERRIDE_KEY, APPEARANCE_DEFAULTS, THEME_KEY, Theme
from app.domain.time import to_ms
from app.logger import get_logger
from app.repositories.app_settings import SettingChange, SettingRow, app_settings_repository
from app.schemas.appearance import AppearanceIn, AppearanceOut
from app.services.cache import Cached, etag_for, etag_matches

logger = get_logger(__name__)

_KEYS = list(APPEARANCE_DEFAULTS)


def _appearance_out(rows: list[SettingRow]) -> AppearanceOut:
    values = {**APPEARANCE_DEFAULTS, **{row.key: row.value for row in rows}}
    latest = max(rows, key=lambda row: row.updated_at, default=None)
    return AppearanceOut(
        theme=Theme(values[THEME_KEY]),
        allow_user_override=bool(values[ALLOW_OVERRIDE_KEY]),
        updated_at=None if latest is None else to_ms(latest.updated_at),
        updated_by=None if latest is None else latest.updated_by_name,
    )


class AppearanceService:
    """Appearance settings use cases."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, if_none_match: str | None) -> Cached[AppearanceOut]:
        """The current appearance (defaults until an admin changes it), with its ETag."""
        body = await self._read()
        etag = etag_for(0, "appearance", body.model_dump_json())
        if etag_matches(if_none_match, etag):
            return Cached(etag=etag, body=None)
        return Cached(etag=etag, body=body)

    async def update(self, body: AppearanceIn, user: User) -> AppearanceOut:
        """Store the admin's choice. Only changed keys are written and audited; last write wins."""
        wanted = {THEME_KEY: body.theme.value, ALLOW_OVERRIDE_KEY: body.allow_user_override}
        stored = await app_settings_repository.lock_values(self.session, _KEYS)
        current = {**APPEARANCE_DEFAULTS, **stored}
        changes = [
            SettingChange(key=key, old_value=current[key], new_value=value)
            for key, value in wanted.items()
            if current[key] != value
        ]
        if changes:
            await app_settings_repository.save_changes(self.session, changes, user.id)
            await self.session.commit()
            logger.info(
                "appearance changed",
                extra={"keys": [c.key for c in changes], "by": str(user.id)},
            )
        return await self._read()

    async def _read(self) -> AppearanceOut:
        return _appearance_out(await app_settings_repository.read(self.session, _KEYS))
