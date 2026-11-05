"""Admin routes (plan §6.2). Admins only; everyone else gets 403."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import AdminAuth, get_admin_service, get_appearance_service, get_merge_service
from app.schemas.admin import (
    AdminUserRow,
    CreateInviteIn,
    DeletedGamesOut,
    DeletionPreviewOut,
    InviteLinkOut,
    MergeIn,
    OverviewOut,
    PlayerEnvelopeOut,
    ResetLinkOut,
    UpdateUserIn,
    UserEnvelopeOut,
)
from app.schemas.appearance import AppearanceIn, AppearanceOut
from app.services.admin import AdminService
from app.services.appearance import AppearanceService
from app.services.merge import MergeService

_AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
_MergeServiceDep = Annotated[MergeService, Depends(get_merge_service)]
_AppearanceServiceDep = Annotated[AppearanceService, Depends(get_appearance_service)]
_UserId = Annotated[uuid.UUID, Path(description="User id")]

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


@admin_router.get("/users", response_model=list[AdminUserRow])
async def list_users(ctx: AdminAuth, service: _AdminServiceDep) -> list[AdminUserRow]:
    """Every account and pending invite."""
    return await service.list_users(ctx.user)


@admin_router.post("/invites", response_model=InviteLinkOut, status_code=201)
async def create_invite(
    body: CreateInviteIn, ctx: AdminAuth, service: _AdminServiceDep
) -> InviteLinkOut:
    """A copyable invite link (replaces any pending invite for the email)."""
    return await service.create_invite(body, ctx.user)


@admin_router.delete("/invites/{email}", status_code=204)
async def revoke_invite(
    email: Annotated[str, Path(max_length=254)], _: AdminAuth, service: _AdminServiceDep
) -> None:
    """Revoke the pending invite for an email."""
    await service.revoke_invite(email)


@admin_router.post("/users/{user_id}/reset-link", response_model=ResetLinkOut, status_code=201)
async def create_reset_link(
    user_id: _UserId, _: AdminAuth, service: _AdminServiceDep
) -> ResetLinkOut:
    """A copyable one-time password reset link (24 hours)."""
    return await service.create_reset_link(user_id)


@admin_router.patch("/users/{user_id}", response_model=UserEnvelopeOut)
async def update_user(
    user_id: _UserId, body: UpdateUserIn, _: AdminAuth, service: _AdminServiceDep
) -> UserEnvelopeOut:
    """Switch an account on or off."""
    return await service.update_user(user_id, body)


@admin_router.get("/users/{user_id}/deletion-preview", response_model=DeletionPreviewOut)
async def get_deletion_preview(
    user_id: _UserId, _: AdminAuth, service: _AdminServiceDep
) -> DeletionPreviewOut:
    """What clearing the user's games, or deleting the user, would remove."""
    return await service.deletion_preview(user_id)


@admin_router.delete("/users/{user_id}/games", response_model=DeletedGamesOut)
async def clear_user_games(
    user_id: _UserId, ctx: AdminAuth, service: _AdminServiceDep
) -> DeletedGamesOut:
    """Delete every game the user scored (their account and players stay)."""
    return await service.clear_games(user_id, ctx.user)


@admin_router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: _UserId, ctx: AdminAuth, service: _AdminServiceDep) -> None:
    """Delete an account and everything it scored; a player others' games use is anonymised."""
    await service.delete_user(user_id, ctx.user)


@admin_router.post("/players/{player_id}/merge", response_model=PlayerEnvelopeOut)
async def merge_player(
    player_id: Annotated[uuid.UUID, Path(description="The player that goes away")],
    body: MergeIn,
    ctx: AdminAuth,
    service: _MergeServiceDep,
) -> PlayerEnvelopeOut:
    """Merge a duplicate player into another; games, badges, email and photo move with it.

    The duplicate's names and aliases become aliases of the kept player; `keep` picks which
    details survive.
    """
    return await service.merge(player_id, body.into_id, ctx.user, keep=body.keep)


@admin_router.get("/overview", response_model=OverviewOut)
async def get_overview(_: AdminAuth, service: _AdminServiceDep) -> OverviewOut:
    """Counts and the last backup time."""
    return await service.overview()


@admin_router.put("/settings/appearance", response_model=AppearanceOut)
async def set_appearance(
    body: AppearanceIn, ctx: AdminAuth, service: _AppearanceServiceDep
) -> AppearanceOut:
    """Set the theme for everyone and whether players may choose their own."""
    return await service.update(body, ctx.user)
