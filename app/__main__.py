"""`python -m app`: run the API under uvicorn (systemd runs exactly this)."""

import uvicorn

from app.settings import settings

APP_MODULE = "app.bootstrap.app:app"

if __name__ == "__main__":
    reload = settings.APP_ENV == "local"
    uvicorn.run(
        APP_MODULE,
        host=settings.HOST,
        port=settings.PORT,
        workers=1 if reload else settings.WEB_CONCURRENCY,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
        log_config=None,
    )
