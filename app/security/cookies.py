"""The `kf_session` cookie."""

from starlette.responses import Response

from app.settings import settings

SESSION_COOKIE = "kf_session"


def set_session_cookie(response: Response, token: str) -> None:
    """HttpOnly, Secure (outside local/test), SameSite=Lax, Path=/, rolling max-age."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.SESSION_DAYS * 86400,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the cookie."""
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
