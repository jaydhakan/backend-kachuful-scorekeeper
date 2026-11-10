"""Sign-in, sessions, invites, resets and password changes."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, Player, User
from app.domain.time import now_utc
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.app_state import app_state_repository
from app.repositories.invites import invites_repository
from app.repositories.players import PlayerView, players_repository
from app.repositories.resets import resets_repository
from app.repositories.sessions import sessions_repository
from app.repositories.users import users_repository
from app.schemas.auth import (
    AcceptInviteIn,
    AuthOut,
    ChangePasswordIn,
    InviteInfoOut,
    LoginIn,
    PreferencesIn,
    PreferencesOut,
    ResetIn,
    ResetInfoOut,
)
from app.schemas.players import NewPlayerIn
from app.security.passwords import hash_password, is_valid_password, needs_rehash, verify_password
from app.security.tokens import hash_token, new_token
from app.services.players import PlayersService, email_taken
from app.services.presenters import (
    player_out,
    player_summary_out,
    preferences_out,
    user_out,
)
from app.settings import settings

logger = get_logger(__name__)

# Session bookkeeping writes at most this often per session.
_TOUCH_EVERY = timedelta(minutes=5)
_EXTEND_EVERY = timedelta(days=1)


@dataclass(frozen=True)
class AuthContext:
    """The signed-in user of a request."""

    user: User
    session_id: uuid.UUID
    token: str
    reissue_cookie: bool


@dataclass(frozen=True)
class SignedIn:
    """A new session: the token for the cookie and the `{user, player}` body."""

    token: str
    body: AuthOut


def _gone() -> ApiError:
    return ApiError(410, ErrorCode.GONE, "This link has been used or has expired.")


def _weak_password() -> ApiError:
    return ApiError(
        422,
        ErrorCode.WEAK_PASSWORD,
        "Use at least 3 characters and no spaces.",
        fields={"password": "at least 3 characters, no spaces, at most 128"},
    )


def _bad_credentials() -> ApiError:
    return ApiError(401, ErrorCode.BAD_CREDENTIALS, "Wrong email or password.")


class AuthService:
    """Authentication use cases."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate(self, token: str) -> AuthContext | None:
        """The session for a cookie token, refreshed lazily; None when invalid."""
        now = now_utc()
        found = await sessions_repository.get_live_with_user(self.session, hash_token(token), now)
        # End the read here either way: a request that holds this connection while its route asks the
        # pool for another (rate limits use their own session) deadlocks a busy pool.
        if found is None:
            await self.session.rollback()
            return None
        row, user = found
        if not user.active:
            await self.session.rollback()
            return None
        lifetime = timedelta(days=settings.SESSION_DAYS)
        extend = row.expires_at - now < lifetime - _EXTEND_EVERY
        if extend or now - row.last_used_at > _TOUCH_EVERY:
            await sessions_repository.refresh(
                self.session,
                row.id,
                last_used_at=now,
                expires_at=now + lifetime if extend else None,
            )
            await users_repository.touch_last_seen(self.session, user.id, now)
        await self.session.commit()
        return AuthContext(user=user, session_id=row.id, token=token, reissue_cookie=extend)

    async def _start_session(self, user: User, user_agent: str | None) -> str:
        token = new_token()
        now = now_utc()
        await sessions_repository.create(
            self.session,
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(days=settings.SESSION_DAYS),
            user_agent=(user_agent or "")[:300] or None,
        )
        await users_repository.touch_last_seen(self.session, user.id, now)
        return token

    async def auth_body(self, user: User) -> AuthOut:
        """`{user, player}` for a user."""
        view = None
        if user.player_id is not None:
            view = await players_repository.get_view(self.session, user.player_id)
        player = None if view is None else player_out(view, user)
        return AuthOut(user=user_out(user), player=player, preferences=preferences_out(user))

    async def update_preferences(self, user: User, body: PreferencesIn) -> PreferencesOut:
        """Replace the preferences the body names; the ones it leaves out stay as they are."""
        changes = body.model_dump(mode="json", include=body.model_fields_set)
        if not changes:
            return preferences_out(user)
        updated = await users_repository.merge_preferences(self.session, user.id, changes)
        await self.session.commit()
        return preferences_out(updated)

    async def login(self, body: LoginIn, user_agent: str | None) -> SignedIn:
        """Email + password. Same error for an unknown email and a wrong password."""
        user = await users_repository.get_by_email(self.session, body.email.strip())
        if not verify_password(user.password_hash if user else None, body.password):
            raise _bad_credentials()
        assert user is not None and user.password_hash is not None
        if not user.active:
            raise ApiError(403, ErrorCode.INACTIVE, "This account has been switched off.")
        if needs_rehash(user.password_hash):
            await users_repository.set_password(self.session, user, hash_password(body.password))
        token = await self._start_session(user, user_agent)
        await self.session.commit()
        return SignedIn(token=token, body=await self.auth_body(user))

    async def logout(self, ctx: AuthContext | None) -> None:
        """Delete the current session, if any."""
        if ctx is not None:
            await sessions_repository.delete(self.session, ctx.session_id)
            await self.session.commit()

    async def _usable_invite(self, token: str) -> Invite:
        invite = await invites_repository.get_usable(self.session, hash_token(token), now_utc())
        if invite is None:
            raise _gone()
        return invite

    async def invite_info(self, token: str) -> InviteInfoOut:
        """Who an invite is for, and their player: the pre-chosen one, else the one with this email."""
        invite = await self._usable_invite(token)
        view = None
        if invite.player_id is not None:
            view = await players_repository.get_view(self.session, invite.player_id)
        else:
            view = await players_repository.unlinked_by_email(self.session, invite.email)
        return InviteInfoOut(
            email=invite.email, player=None if view is None else player_summary_out(view)
        )

    async def accept_invite(
        self, token: str, body: AcceptInviteIn, user_agent: str | None
    ) -> SignedIn:
        """Set a password, pick or create the player, create the account and sign in."""
        invite = await self._usable_invite(token)
        if not is_valid_password(body.password):
            raise _weak_password()
        user = await users_repository.get_by_email(self.session, invite.email)
        if user is not None and user.password_hash is not None:
            raise _gone()
        creator_id = user.id if user is not None else None
        if user is None:
            user = await users_repository.create(
                self.session,
                email=invite.email,
                role="member",
                password_hash=None,
                player_id=None,
            )
            creator_id = user.id
        assert creator_id is not None
        view = await self._invitee_player(invite, body, created_by=creator_id)
        user.password_hash = hash_password(body.password)
        user.player_id = view.player.id
        now = now_utc()
        await invites_repository.mark_accepted(self.session, invite, now)
        await invites_repository.revoke_pending(self.session, invite.email, now)
        await achievements_repository.add_activity(
            self.session, [{"kind": "player_joined", "player_id": view.player.id, "data": {}}]
        )
        await app_state_repository.bump(self.session)
        token_out = await self._start_session(user, user_agent)
        await self.session.commit()
        logger.info("invite accepted", extra={"user_id": str(user.id)})
        return SignedIn(token=token_out, body=await self.auth_body(user))

    async def _invitee_player(
        self, invite: Invite, body: AcceptInviteIn, *, created_by: uuid.UUID
    ) -> PlayerView:
        chosen = body.player
        if invite.player_id is not None and chosen.id != invite.player_id:
            raise ApiError(
                422,
                ErrorCode.INVALID,
                "This invite is for a specific player.",
                fields={"player.id": "must be the invite's player"},
            )
        if isinstance(chosen, NewPlayerIn):
            # A new player always gets the invite's email; a full match is never revealed here.
            created = await PlayersService(self.session).create(
                chosen, created_by=created_by, email=invite.email, reveal_match=False
            )
            return created.view
        found = await players_repository.get_view(self.session, chosen.id)
        if found is None or found.player.merged_into is not None:
            raise ApiError(
                422, ErrorCode.UNKNOWN_PLAYER, "No such player.", playerIds=[str(chosen.id)]
            )
        if found.linked_user_id is not None:
            raise ApiError(409, ErrorCode.PLAYER_LINKED, "That player already has an account.")
        await self._claim_email(found.player, invite)
        return found

    async def _claim_email(self, player: Player, invite: Invite) -> None:
        """A claimed player gets the invite's email if it has none; another person's is refused."""
        if player.email is None:
            holder = await players_repository.get_active_by_email(self.session, invite.email)
            if holder is not None and holder.id != player.id:
                raise email_taken()
            player.email = invite.email
            await players_repository.touch(self.session, player)
        elif player.email.lower() != invite.email.lower() and invite.player_id is None:
            # Unless the admin chose this player for the invite, it must be the invitee's own.
            raise email_taken()

    async def reset_info(self, token: str) -> ResetInfoOut:
        """Whose password a reset link is for."""
        reset = await resets_repository.get_usable(self.session, hash_token(token), now_utc())
        if reset is None:
            raise _gone()
        user = await users_repository.get(self.session, reset.user_id)
        assert user is not None
        return ResetInfoOut(email=user.email)

    async def reset_password(self, token: str, body: ResetIn, user_agent: str | None) -> SignedIn:
        """Set a new password, revoke every other session, sign in here."""
        now = now_utc()
        reset = await resets_repository.get_usable(self.session, hash_token(token), now)
        if reset is None:
            raise _gone()
        if not is_valid_password(body.password):
            raise _weak_password()
        user = await users_repository.get(self.session, reset.user_id)
        assert user is not None
        if not user.active:
            raise ApiError(403, ErrorCode.INACTIVE, "This account has been switched off.")
        await users_repository.set_password(self.session, user, hash_password(body.password))
        await resets_repository.mark_used(self.session, reset, now)
        await sessions_repository.delete_for_user(self.session, user.id)
        token_out = await self._start_session(user, user_agent)
        await self.session.commit()
        return SignedIn(token=token_out, body=await self.auth_body(user))

    async def change_password(self, ctx: AuthContext, body: ChangePasswordIn) -> None:
        """Change the password; every other session is revoked."""
        user = ctx.user
        if not verify_password(user.password_hash, body.current):
            raise _bad_credentials()
        if not is_valid_password(body.password):
            raise _weak_password()
        await users_repository.set_password(self.session, user, hash_password(body.password))
        await sessions_repository.delete_for_user(self.session, user.id, keep=ctx.session_id)
        await self.session.commit()
