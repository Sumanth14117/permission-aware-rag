"""Minimal FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import router as auth_router
from app.config import Settings, get_settings
from app.csrf import (
    get_single_raw_header,
    get_unique_raw_cookie,
    is_allowed_origin,
    validate_double_submit,
)
from app.documents import router as documents_router
from app.qa import router as qa_router

APP_TITLE = "Permission-Aware Multi-Tenant RAG Platform"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the API application without opening a database connection."""
    app_settings = settings if settings is not None else get_settings()
    application = FastAPI(title=APP_TITLE)
    application.state.settings = app_settings
    application.include_router(auth_router)
    application.include_router(documents_router)
    application.include_router(qa_router)

    @application.middleware("http")
    async def protect_unsafe_requests(request: Request, call_next):
        # Every unsafe method is protected by default; add no route exemptions here.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            settings = request.app.state.settings
            origin, origin_ok = get_single_raw_header(request.scope, b"origin")
            csrf_header, csrf_header_ok = get_single_raw_header(
                request.scope, b"x-csrf-token"
            )
            csrf_cookie, csrf_cookie_ok = get_unique_raw_cookie(
                request.scope, settings.csrf_cookie_name
            )
            if (
                not origin_ok
                or not csrf_header_ok
                or not csrf_cookie_ok
                or not is_allowed_origin(origin, settings.cors_origin_list)
                or not validate_double_submit(
                    csrf_cookie,
                    csrf_header,
                    settings.csrf_secret.get_secret_value(),
                )
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF validation failed"},
                )
        return await call_next(request)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Return process health without checking external services."""
        return {"status": "ok"}

    return application


app = create_app()
