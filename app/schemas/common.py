"""Shared building blocks for request and response models."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class Page[T](CamelModel):
    """A cursor page: `{items, nextCursor}`."""

    items: list[T] = Field(..., description="This page")
    next_cursor: str | None = Field(
        ..., description="Pass as ?cursor= for the next page; null at the end"
    )
