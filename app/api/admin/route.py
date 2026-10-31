"""Admin routes (plan §6.2). Admins only; everyone else gets 403."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import AdminAuth, get_admin_service, get_merge_service
from app.schemas.admin import (
    AdminUserRow,
    CreateInviteIn,
    InviteLinkOut,
    MergeIn,
    OverviewOut,
    PlayerEnvelopeOut,
    ResetLinkOut,
    UpdateUserIn,
    UserEnvelopeOut,
)
from app.services.admin import AdminService
from app.services.merge import MergeService

_AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
_MergeServiceDep = Annotated[MergeService, Depends(get_merge_service)]
_UserId = Annotated[uuid.UUID, Path(description="User id")]

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


@admin_router.get("/users", response_model=list[AdminUserRow])
async def list_users(_: AdminAuth, service: _AdminServiceDep) -> list[AdminUserRow]:
    """Every account and pending invite."""
    return await service.list_users()


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


@admin_router.post("/players/{player_id}/merge", response_model=PlayerEnvelopeOut)
async def merge_player(
    player_id: Annotated[uuid.UUID, Path(description="The player that goes away")],
    body: MergeIn,
    _: AdminAuth,
    service: _MergeServiceDep,
) -> PlayerEnvelopeOut:
    """Merge a duplicate player into another; every game and badge moves with it."""
    return await service.merge(player_id, body.into_id)


@admin_router.get("/overview", response_model=OverviewOut)
async def get_overview(_: AdminAuth, service: _AdminServiceDep) -> OverviewOut:
    """Counts and the last backup time."""
    return await service.overview()
