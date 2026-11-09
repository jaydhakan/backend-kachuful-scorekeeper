"""The FastAPI application: middleware, error handlers and routers."""

from fastapi import FastAPI

from app.api.admin import admin_router
from app.api.appearance import appearance_router
from app.api.auth import auth_router, me_router
from app.api.boards import boards_router
from app.api.claims import claims_router
from app.api.core import core_router
from app.api.games import games_router, me_data_router
from app.api.kaali_teeri import kaali_teeri_router
from app.api.pages import pages_router
from app.api.players import players_router
from app.api.public import public_router
from app.bootstrap.lifespan import lifespan
from app.bootstrap.middleware import AccessLogMiddleware, BodySizeLimitMiddleware
from app.exceptions import register_exception_handlers
from app.security.csrf import CsrfMiddleware
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
    # Last added runs first: log → size limit → CSRF → routes.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    for router in (
        core_router,
        auth_router,
        me_router,
        admin_router,
        appearance_router,
        players_router,
        games_router,
        me_data_router,
        kaali_teeri_router,
        claims_router,
        boards_router,
        public_router,
    ):
        app.include_router(router, prefix="/api")
    app.include_router(pages_router)
    return app


app = create_app()
