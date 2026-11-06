"""What deleting an account touches: counts for the preview, and the references that must move first."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, distinct, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    Activity,
    AdminAudit,
    Game,
    GamePlayer,
    Invite,
    Player,
    PlayerAlias,
    PlayerPhoto,
    Session,
    ShareLink,
    User,
)


@dataclass(frozen=True)
class AccountFootprint:
    """The counts a deletion preview shows."""

    games: int
    rounds: int
    other_players: int
    sessions: int
    invites: int
    has_photo: bool
    player_in_others_games: bool


class AccountsRepository:
    """Data access for deleting an account."""

    async def footprint(self, session: AsyncSession, user: User) -> AccountFootprint:
        """Everything the preview counts, in a handful of small queries."""
        owned = Game.owner_id == user.id, Game.deleted_at.is_(None)
        games, rounds = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(Game.round_count), 0)).where(*owned)
            )
        ).one()
        others = select(func.count(distinct(GamePlayer.player_id))).where(
            GamePlayer.game_id.in_(select(Game.id).where(*owned))
        )
        if user.player_id is not None:
            others = others.where(GamePlayer.player_id != user.player_id)
        sessions = select(func.count()).select_from(Session).where(Session.user_id == user.id)
        invites = select(func.count()).select_from(Invite).where(Invite.email == user.email)
        photo = in_others = False
        if user.player_id is not None:
            photo = bool(
                (
                    await session.execute(
                        select(exists().where(PlayerPhoto.player_id == user.player_id))
                    )
                ).scalar()
            )
            in_others = bool(
                (
                    await session.execute(
                        select(
                            exists().where(
                                GamePlayer.player_id == user.player_id,
                                GamePlayer.game_id == Game.id,
                                Game.owner_id != user.id,
                                Game.deleted_at.is_(None),
                            )
                        )
                    )
                ).scalar()
            )
        return AccountFootprint(
            games=int(games),
            rounds=int(rounds),
            other_players=int((await session.execute(others)).scalar_one()),
            sessions=int((await session.execute(sessions)).scalar_one()),
            invites=int((await session.execute(invites)).scalar_one()),
            has_photo=photo,
            player_in_others_games=in_others,
        )

    async def hand_over(self, session: AsyncSession, user: User, admin_id: uuid.UUID) -> None:
        """Point what must outlive the account (its deleted games, their links) at the admin."""
        await session.execute(
            update(Game).where(Game.owner_id == user.id).values(owner_id=admin_id)
        )
        await session.execute(
            update(ShareLink).where(ShareLink.created_by == user.id).values(created_by=admin_id)
        )
        await session.execute(
            update(Player).where(Player.created_by == user.id).values(created_by=None)
        )
        await session.execute(
            update(Invite).where(Invite.created_by == user.id).values(created_by=None)
        )
        await session.execute(
            update(PlayerAlias).where(PlayerAlias.created_by == user.id).values(created_by=None)
        )
        await session.execute(delete(Invite).where(Invite.email == user.email))

    async def player_in_use(self, session: AsyncSession, player_id: uuid.UUID) -> bool:
        """Any game, invite, account or merge still points at the player."""
        merged_here = select(Player.id).where(Player.merged_into == player_id).exists()
        stmt = select(
            exists().where(GamePlayer.player_id == player_id)
            | exists().where(Invite.player_id == player_id)
            | exists().where(User.player_id == player_id)
            | merged_here
        )
        return bool((await session.execute(stmt)).scalar())

    async def anonymise(self, session: AsyncSession, player: Player) -> None:
        """Strip the person from a player other people's games still use."""
        player.display_name = "Deleted player"
        # Unique among active players, and no typed name ever produces it.
        player.name_key = f"deleted:{player.id}"
        player.email = player.first_name = player.last_name = None
        player.anonymised_at = func.now()
        player.updated_at = func.now()
        player.photo_rev += 1
        await session.execute(delete(PlayerPhoto).where(PlayerPhoto.player_id == player.id))
        # Other names are the person too.
        await session.execute(delete(PlayerAlias).where(PlayerAlias.player_id == player.id))
        await session.execute(
            delete(Activity).where(
                Activity.player_id == player.id, Activity.kind == "player_joined"
            )
        )
        await session.flush()

    async def delete_player(self, session: AsyncSession, player_id: uuid.UUID) -> None:
        """Remove a player nothing uses, with its badges and feed rows."""
        await session.execute(delete(Activity).where(Activity.player_id == player_id))
        await session.execute(delete(Achievement).where(Achievement.player_id == player_id))
        await session.execute(delete(Player).where(Player.id == player_id))

    async def delete_user(self, session: AsyncSession, user: User) -> None:
        """Remove the row; sessions and reset links go with it (cascade)."""
        await session.delete(user)
        await session.flush()

    async def audit(
        self, session: AsyncSession, actor_id: uuid.UUID, action: str, target: str, details: Any
    ) -> None:
        """Record a destructive admin action."""
        session.add(
            AdminAudit(actor_user_id=actor_id, action=action, target=target, details=details)
        )
        await session.flush()


accounts_repository = AccountsRepository()
