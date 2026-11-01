"""Auth routes (plan §6.1)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response

from app.api.deps import (
    CurrentAuth,
    OptionalAuth,
    client_ip,
    get_auth_service,
    user_agent,
)
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
from app.security import rate_limit
from app.security.cookies import clear_session_cookie, set_session_cookie
from app.services.auth import AuthService

_AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
_Token = Annotated[str, Path(min_length=10, max_length=100, description="One-time token")]

auth_router = APIRouter(prefix="/auth", tags=["Auth"])
me_router = APIRouter(prefix="/me", tags=["Auth"])


@auth_router.post("/login", response_model=AuthOut)
async def login(
    body: LoginIn, request: Request, response: Response, service: _AuthServiceDep
) -> AuthOut:
    """Sign in with email and password; sets the `kf_session` cookie."""
    await rate_limit.enforce(rate_limit.LOGIN, f"ip:{client_ip(request)}")
    await rate_limit.enforce(rate_limit.LOGIN, f"email:{body.email.strip().lower()}")
    signed_in = await service.login(body, user_agent(request))
    set_session_cookie(response, signed_in.token)
    return signed_in.body


@auth_router.post("/logout", status_code=204)
async def logout(ctx: OptionalAuth, response: Response, service: _AuthServiceDep) -> None:
    """End this session and clear the cookie."""
    await service.logout(ctx)
    clear_session_cookie(response)


@auth_router.get("/me", response_model=AuthOut)
async def get_me(ctx: CurrentAuth, service: _AuthServiceDep) -> AuthOut:
    """The signed-in user and their player."""
    return await service.auth_body(ctx.user)


@auth_router.get("/invites/{token}", response_model=InviteInfoOut)
async def get_invite(token: _Token, request: Request, service: _AuthServiceDep) -> InviteInfoOut:
    """Who an invite link is for."""
    await rate_limit.enforce(rate_limit.TOKEN, f"ip:{client_ip(request)}")
    return await service.invite_info(token)


@auth_router.post("/invites/{token}", response_model=AuthOut)
async def accept_invite(
    token: _Token,
    body: AcceptInviteIn,
    request: Request,
    response: Response,
    service: _AuthServiceDep,
) -> AuthOut:
    """Accept an invite: set a password, choose a player, get signed in."""
    await rate_limit.enforce(rate_limit.TOKEN, f"ip:{client_ip(request)}")
    signed_in = await service.accept_invite(token, body, user_agent(request))
    set_session_cookie(response, signed_in.token)
    return signed_in.body


@auth_router.get("/resets/{token}", response_model=ResetInfoOut)
async def get_reset(token: _Token, request: Request, service: _AuthServiceDep) -> ResetInfoOut:
    """Whose password a reset link is for."""
    await rate_limit.enforce(rate_limit.TOKEN, f"ip:{client_ip(request)}")
    return await service.reset_info(token)


@auth_router.post("/resets/{token}", response_model=AuthOut)
async def reset_password(
    token: _Token,
    body: ResetIn,
    request: Request,
    response: Response,
    service: _AuthServiceDep,
) -> AuthOut:
    """Set a new password from a reset link; every other session is signed out."""
    await rate_limit.enforce(rate_limit.TOKEN, f"ip:{client_ip(request)}")
    signed_in = await service.reset_password(token, body, user_agent(request))
    set_session_cookie(response, signed_in.token)
    return signed_in.body


@me_router.post("/password", status_code=204)
async def change_password(
    body: ChangePasswordIn, ctx: CurrentAuth, service: _AuthServiceDep
) -> None:
    """Change the password; other sessions are signed out."""
    await service.change_password(ctx, body)


@me_router.patch("/preferences", response_model=PreferencesOut)
async def update_preferences(
    body: PreferencesIn, ctx: CurrentAuth, service: _AuthServiceDep
) -> PreferencesOut:
    """Save the signed-in user's theme and setup defaults; fields left out are kept."""
    return await service.update_preferences(ctx.user, body)
