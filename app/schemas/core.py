"""Health check models."""

from pydantic import Field

from app.schemas.common import CamelModel


class Health(CamelModel):
    """`GET /api/health` (plan §11)."""

    ok: bool = Field(..., description="True when the API and its database both answer")
    db: bool = Field(..., description="True when a database round trip succeeded")
    version: str = Field(..., description="Deployed version")
