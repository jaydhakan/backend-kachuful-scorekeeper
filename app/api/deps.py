"""FastAPI dependencies: the request's DB session, the signed-in user and one factory per service."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.exceptions import ApiError, ErrorCode
from app.security.cookies import SESSION_COOKIE, set_session_cookie
from app.services.admin import AdminService
from app.services.auth import AuthContext, AuthService
from app.services.core import CoreService
from app.services.games import GamesService
from app.services.merge import MergeService
from app.services.players import PlayersService


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request; services commit, anything uncommitted is rolled back."""
    async with get_session_factory()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    """The caller's IP (uvicorn resolves X-Forwarded-For from the local nginx only)."""
    return request.client.host if request.client else "unknown"


def user_agent(request: Request) -> str | None:
    """The caller's User-Agent header."""
    return request.headers.get("user-agent")


async def get_optional_auth(
    request: Request, response: Response, session: SessionDep
) -> AuthContext | None:
    """The signed-in user, or None. Re-issues the cookie when the session was extended."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    ctx = await AuthService(session).authenticate(token)
    if ctx is not None and ctx.reissue_cookie:
        set_session_cookie(response, ctx.token)
    return ctx


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]


async def get_auth(ctx: OptionalAuth) -> AuthContext:
    """The signed-in user; 401 `unauthenticated` otherwise."""
    if ctx is None:
        raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Please sign in.")
    return ctx


CurrentAuth = Annotated[AuthContext, Depends(get_auth)]


async def get_admin(ctx: CurrentAuth) -> AuthContext:
    """The signed-in admin; 403 `forbidden` for everyone else."""
    if ctx.user.role != "admin":
        raise ApiError(403, ErrorCode.FORBIDDEN, "Admins only.")
    return ctx


AdminAuth = Annotated[AuthContext, Depends(get_admin)]


def get_core_service() -> CoreService:
    """CoreService for a request."""
    return CoreService()


def get_auth_service(session: SessionDep) -> AuthService:
    """AuthService for a request."""
    return AuthService(session)


def get_admin_service(session: SessionDep) -> AdminService:
    """AdminService for a request."""
    return AdminService(session)


def get_players_service(session: SessionDep) -> PlayersService:
    """PlayersService for a request."""
    return PlayersService(session)


def get_merge_service(session: SessionDep) -> MergeService:
    """MergeService for a request."""
    return MergeService(session)


def get_games_service(session: SessionDep) -> GamesService:
    """GamesService for a request."""
    return GamesService(session)
