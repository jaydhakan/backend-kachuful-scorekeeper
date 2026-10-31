from typing import Annotated

import httpx
from fastapi import FastAPI, Query

from app.bootstrap.middleware import BodySizeLimitMiddleware
from app.exceptions import ApiError, ErrorCode, register_exception_handlers
from app.schemas.common import CamelModel


class _Body(CamelModel):
    display_name: str


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)

    @app.post("/echo")
    async def echo(body: _Body, limit: Annotated[int, Query(ge=1)] = 5) -> dict[str, str]:
        return {"name": body.display_name}

    @app.get("/teapot")
    async def teapot() -> None:
        raise ApiError(409, ErrorCode.NAME_TAKEN, "Taken.", existing={"id": "x"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret SQL here")

    return app


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_api_error_returns_code_message_and_extras() -> None:
    async with _client() as c:
        response = await c.get("/teapot")

    assert response.status_code == 409
    assert response.json() == {"code": "name_taken", "message": "Taken.", "existing": {"id": "x"}}


async def test_request_validation_returns_invalid_with_camel_case_fields() -> None:
    async with _client() as c:
        response = await c.post("/echo?limit=0", json={"displayName": 3})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid"
    assert set(body["fields"]) == {"displayName", "limit"}


async def test_unknown_route_returns_not_found() -> None:
    async with _client() as c:
        response = await c.get("/nope")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_unhandled_error_returns_internal_without_details() -> None:
    async with _client() as c:
        response = await c.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"code": "internal", "message": "Something went wrong."}


async def test_body_over_one_megabyte_returns_too_large() -> None:
    async with _client() as c:
        response = await c.post("/echo", content=b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["code"] == "too_large"
