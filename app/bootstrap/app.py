"""The FastAPI application: middleware, error handlers and routers."""

from fastapi import FastAPI

from app.api.core import core_router
from app.bootstrap.lifespan import lifespan
from app.bootstrap.middleware import AccessLogMiddleware, BodySizeLimitMiddleware
from app.exceptions import register_exception_handlers
from app.settings import settings


def create_app() -> FastAPI:
    """Build the app; tests call this too."""
    show_docs = settings.APP_ENV != "production"
    app = FastAPI(
        title="Kachuful API",
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/api/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if show_docs else None,
    )
    register_exception_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.include_router(core_router, prefix="/api")
    return app


app = create_app()
