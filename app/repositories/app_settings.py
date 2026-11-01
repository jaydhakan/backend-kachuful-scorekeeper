"""App-wide settings and their audit trail."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, AppSettingAudit, Player, User


@dataclass(frozen=True)
class SettingRow:
    """A stored setting and the player name of the user who last changed it."""

    key: str
    value: Any
    updated_at: datetime
    updated_by_name: str | None


@dataclass(frozen=True)
class SettingChange:
    """One setting moving from its value in effect to a new one."""

    key: str
    old_value: Any
    new_value: Any


class AppSettingsRepository:
    """Data access for `app_settings` and `app_settings_audit`."""

    async def read(self, session: AsyncSession, keys: Sequence[str]) -> list[SettingRow]:
        """The stored rows among `keys` (missing keys were never changed), in one query."""
        stmt = (
            select(AppSetting.key, AppSetting.value, AppSetting.updated_at, Player.display_name)
            .outerjoin(User, User.id == AppSetting.updated_by_user_id)
            .outerjoin(Player, Player.id == User.player_id)
            .where(AppSetting.key.in_(keys))
        )
        return [SettingRow(*row) for row in (await session.execute(stmt)).all()]

    async def lock_values(self, session: AsyncSession, keys: Sequence[str]) -> dict[str, Any]:
        """Stored values among `keys`, locked until the transaction ends."""
        stmt = (
            select(AppSetting.key, AppSetting.value)
            .where(AppSetting.key.in_(keys))
            .with_for_update()
        )
        return {row.key: row.value for row in (await session.execute(stmt)).all()}

    async def save_changes(
        self, session: AsyncSession, changes: Sequence[SettingChange], user_id: uuid.UUID
    ) -> None:
        """Store the new values and one audit row per change (two statements)."""
        upsert = pg_insert(AppSetting).values(
            [{"key": c.key, "value": c.new_value, "updated_by_user_id": user_id} for c in changes]
        )
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[AppSetting.key],
                set_={
                    "value": upsert.excluded.value,
                    "updated_at": func.now(),
                    "updated_by_user_id": upsert.excluded.updated_by_user_id,
                },
            )
        )
        await session.execute(
            insert(AppSettingAudit),
            [
                {
                    "key": c.key,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "changed_by_user_id": user_id,
                }
                for c in changes
            ],
        )


app_settings_repository = AppSettingsRepository()
