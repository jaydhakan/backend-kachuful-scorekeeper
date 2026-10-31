"""Health check route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_core_service
from app.schemas.core import Health
from app.services.core import CoreService

_CoreServiceDep = Annotated[CoreService, Depends(get_core_service)]

core_router = APIRouter(tags=["Core"])


@core_router.get("/health", response_model=Health)
async def get_health(service: _CoreServiceDep, response: Response) -> Health:
    """Uptime check: 200 when healthy, 503 when the database is unreachable."""
    health = await service.health()
    if not health.ok:
        response.status_code = 503
    return health
