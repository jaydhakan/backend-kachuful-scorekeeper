"""Admin use cases: users, invites, reset links."""

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, User
from app.domain.names import clean_email
from app.domain.time import now_utc, to_ms, to_ms_or_none
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.accounts import accounts_repository
from app.repositories.app_state import app_state_repository
from app.repositories.games import games_repository
from app.repositories.invites import invites_repository
from app.repositories.players import PlayerView, players_repository
from app.repositories.resets import resets_repository
from app.repositories.sessions import sessions_repository
from app.repositories.users import users_repository
from app.schemas.admin import (
    AdminUserRow,
    AttentionOut,
    CreateInviteIn,
    DeletedGamesOut,
    DeletionPreviewOut,
    DuplicatePairOut,
    InviteLinkOut,
    OverviewOut,
    PendingInviteOut,
    ResetLinkOut,
    UpdateUserIn,
    UserEnvelopeOut,
)
from app.schemas.players import PlayerOut
from app.security.tokens import hash_token, new_token
from app.services.games import GamesService
from app.services.presenters import player_out, player_summary_out, user_out
from app.settings import settings

logger = get_logger(__name__)

INVITE_TTL = timedelta(days=7)
INVITE_SOON = timedelta(hours=48)
# Trigram similarity at which two name keys look like one person ("Riya" ~ "Riyaa" is 0.57).
DUPLICATE_SIMILARITY = 0.4
DUPLICATE_LIMIT = 20
RESET_TTL = timedelta(hours=24)


def normalize_email(raw: str) -> str:
    """Trimmed and lower-cased; 422 `invalid` when it can't be an email."""
    email = clean_email(raw)
    if email is None:
        raise ApiError(
            422, ErrorCode.INVALID, "That is not an email address.", fields={"email": "invalid"}
        )
    return email


async def issue_invite(
    session: AsyncSession,
    *,
    email: str,
    player_id: uuid.UUID | None,
    created_by: uuid.UUID | None,
) -> InviteLinkOut:
    """Replace any pending invite for this email with a new one. Does not commit."""
    now = now_utc()
    await invites_repository.revoke_pending(session, email, now)
    token = new_token()
    invite = await invites_repository.create(
        session,
        email=email,
        token_hash=hash_token(token),
        player_id=player_id,
        created_by=created_by,
        expires_at=now + INVITE_TTL,
    )
    return InviteLinkOut(
        invite_url=f"{settings.APP_ORIGIN}/?invite={token}", expires_at=to_ms(invite.expires_at)
    )


def _pending_out(invite: Invite | None) -> PendingInviteOut | None:
    if invite is None:
        return None
    return PendingInviteOut(email=invite.email, expires_at=to_ms(invite.expires_at))


def _player_of(
    views: dict[uuid.UUID, PlayerView], player_id: uuid.UUID | None, admin: User
) -> PlayerOut | None:
    view = views.get(player_id) if player_id else None
    return None if view is None else player_out(view, admin)


class AdminService:
    """Everything behind the admin screen."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_users(self, admin: User) -> list[AdminUserRow]:
        """Every user, then invites that have no account yet."""
        users = await users_repository.list_all(self.session)
        pending: dict[str, Invite] = {}
        for found in await invites_repository.list_usable(self.session, now_utc()):
            pending.setdefault(found.email.lower(), found)
        player_ids = [u.player_id for u in users if u.player_id] + [
            i.player_id for i in pending.values() if i.player_id
        ]
        views = await players_repository.get_views(self.session, player_ids)

        rows = [
            AdminUserRow(
                user=user_out(user),
                player=_player_of(views, user.player_id, admin),
                active=user.active,
                last_seen_at=to_ms_or_none(user.last_seen_at),
                invite=_pending_out(pending.pop(user.email.lower(), None)),
            )
            for user in users
        ]
        rows.extend(
            AdminUserRow(
                user=None,
                player=_player_of(views, invite.player_id, admin),
                active=False,
                last_seen_at=None,
                invite=_pending_out(invite),
            )
            for invite in pending.values()
        )
        return rows

    async def create_invite(self, body: CreateInviteIn, admin: User) -> InviteLinkOut:
        """A copyable invite link; replaces any pending one for the email."""
        email = normalize_email(body.email)
        existing = await users_repository.get_by_email(self.session, email)
        if existing is not None and existing.password_hash is not None:
            raise ApiError(409, ErrorCode.USER_EXISTS, "That email already has an account.")
        if body.player_id is not None:
            await self._check_invitable_player(body.player_id)
        link = await issue_invite(
            self.session, email=email, player_id=body.player_id, created_by=admin.id
        )
        await self.session.commit()
        logger.info("invite created", extra={"by": str(admin.id)})
        return link

    async def _check_invitable_player(self, player_id: uuid.UUID) -> None:
        view = await players_repository.get_view(self.session, player_id)
        if view is None or view.player.merged_into is not None:
            raise ApiError(
                422, ErrorCode.UNKNOWN_PLAYER, "No such player.", playerIds=[str(player_id)]
            )
        if view.linked_user_id is not None:
            raise ApiError(409, ErrorCode.PLAYER_LINKED, "That player already has an account.")

    async def revoke_invite(self, email: str) -> None:
        """Revoke the pending invite for an email (no-op when there is none)."""
        await invites_repository.revoke_pending(self.session, email.strip().lower(), now_utc())
        await self.session.commit()

    async def create_reset_link(self, user_id: uuid.UUID) -> ResetLinkOut:
        """A copyable one-time password reset link."""
        user = await users_repository.get(self.session, user_id)
        if user is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such user.")
        token = new_token()
        reset = await resets_repository.create(
            self.session,
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now_utc() + RESET_TTL,
        )
        await self.session.commit()
        return ResetLinkOut(
            reset_url=f"{settings.APP_ORIGIN}/?reset={token}", expires_at=to_ms(reset.expires_at)
        )

    async def update_user(self, user_id: uuid.UUID, body: UpdateUserIn) -> UserEnvelopeOut:
        """Switch a user on or off; switching off revokes every session."""
        user = await users_repository.get(self.session, user_id)
        if user is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such user.")
        if body.active is False and user.active:
            is_last_admin = (
                user.role == "admin"
                and await users_repository.count_active_admins(self.session) <= 1
            )
            if is_last_admin:
                raise ApiError(409, ErrorCode.LAST_ADMIN, "The last admin can't be switched off.")
            user.active = False
            await sessions_repository.delete_for_user(self.session, user.id)
        elif body.active is True:
            user.active = True
        await self.session.commit()
        return UserEnvelopeOut(user=user_out(user))

    async def overview(self) -> OverviewOut:
        """Counts for the admin dashboard."""
        users, players = await users_repository.counts(self.session)
        games, rounds = await games_repository.counts(self.session)
        last_backup = await app_state_repository.last_backup_at(self.session)
        return OverviewOut(
            users=users,
            players=players,
            games=games,
            rounds=rounds,
            last_backup_at=to_ms_or_none(last_backup),
            attention=await self._attention(),
        )

    async def _attention(self) -> AttentionOut:
        now = now_utc()
        expiring = await invites_repository.count_expiring(self.session, now, now + INVITE_SOON)
        pairs = await players_repository.possible_duplicates(
            self.session, min_similarity=DUPLICATE_SIMILARITY, limit=DUPLICATE_LIMIT
        )
        ids = [i for p in pairs for i in (p.a_id, p.b_id)]
        views = await players_repository.get_views(self.session, ids)
        return AttentionOut(
            invites_expiring_soon=expiring,
            possible_duplicates=[
                DuplicatePairOut(
                    a=player_summary_out(views[p.a_id]),
                    b=player_summary_out(views[p.b_id]),
                    reason="email" if p.reason == "email" else "name",
                )
                for p in pairs
            ],
        )

    async def _target(self, user_id: uuid.UUID) -> User:
        user = await users_repository.get(self.session, user_id)
        if user is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such user.")
        return user

    async def deletion_preview(self, user_id: uuid.UUID) -> DeletionPreviewOut:
        """What clearing the account's games, or deleting the account, removes."""
        f = await accounts_repository.footprint(self.session, await self._target(user_id))
        return DeletionPreviewOut(
            games=f.games,
            rounds=f.rounds,
            other_players=f.other_players,
            sessions=f.sessions,
            invites=f.invites,
            has_photo=f.has_photo,
            player_in_others_games=f.player_in_others_games,
        )

    async def clear_games(self, user_id: uuid.UUID, admin: User) -> DeletedGamesOut:
        """Soft-delete every game the user scored; their players and account stay."""
        user = await self._target(user_id)
        games = await GamesService(self.session).delete_owned(user, admin)
        await accounts_repository.audit(
            self.session, admin.id, "clear_games", user.email, {"games": games}
        )
        await self.session.commit()
        logger.info("admin cleared games", extra={"by": str(admin.id), "games": games})
        return DeletedGamesOut(games=games)

    async def delete_user(self, user_id: uuid.UUID, admin: User) -> None:
        """Delete an account and what it owns; a player other people's games use is anonymised.

        Games go first, each through the one delete use case (its own commit), so a retry after a
        failure picks up where it stopped; the account itself goes in one transaction.
        """
        user = await self._target(user_id)
        if user.id == admin.id:
            raise ApiError(409, ErrorCode.CANNOT_DELETE_SELF, "You can't delete your own account.")
        if user.role == "admin":
            raise ApiError(409, ErrorCode.CANNOT_DELETE_ADMIN, "Admin accounts can't be deleted.")
        footprint = await accounts_repository.footprint(self.session, user)
        games = await GamesService(self.session).delete_owned(user, admin)
        user = await self._target(user_id)
        email, player_id = user.email, user.player_id
        await accounts_repository.hand_over(self.session, user, admin.id)
        players = await players_repository.delete_unused_created_by(self.session, user.id)
        user.player_id = None
        await self.session.flush()
        anonymised = False
        if player_id is not None:
            player = await players_repository.get_for_update(self.session, player_id)
            if player is not None and await accounts_repository.player_in_use(
                self.session, player_id
            ):
                await accounts_repository.anonymise(self.session, player)
                anonymised = True
            elif player is not None:
                await accounts_repository.delete_player(self.session, player_id)
        await accounts_repository.delete_user(self.session, user)
        details = {
            "games": games,
            "rounds": footprint.rounds,
            "players": players,
            "sessions": footprint.sessions,
            "anonymised": anonymised,
        }
        await accounts_repository.audit(self.session, admin.id, "delete_user", email, details)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("admin deleted user", extra={"by": str(admin.id), **details})
