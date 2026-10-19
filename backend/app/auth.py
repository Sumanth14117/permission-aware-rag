"""HTTP routes for login and logout."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.authentication import (
    AuthenticationError,
    AuthenticationServiceError,
    authenticate_and_create_session,
    revoke_session,
)
from app.audit import (
    EVENT_LOGIN_FAILED,
    REASON_AUTHENTICATION_UNAVAILABLE,
    REASON_INVALID_CREDENTIALS,
    record_audit_event,
    record_login_success,
)
from app.csrf import generate_csrf_token, is_valid_bootstrap_origin
from app.db.session import get_db
from app.models.audit_event import OUTCOME_FAILURE

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


@router.get("/csrf")
def csrf_bootstrap(request: Request, response: Response) -> dict[str, str]:
    settings = request.app.state.settings
    if not is_valid_bootstrap_origin(request.scope, settings.cors_origin_list):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    token = generate_csrf_token(
        settings.csrf_secret.get_secret_value(), settings.csrf_ttl
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=int(settings.csrf_ttl.total_seconds()),
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )
    return {"csrf_token": token}


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=int(settings.session_ttl.total_seconds()),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    settings = request.app.state.settings
    try:
        result = authenticate_and_create_session(
            db,
            email=payload.email,
            password=payload.password,
            session_ttl=settings.session_ttl,
        )
    except AuthenticationError:
        record_audit_event(
            db,
            event_type=EVENT_LOGIN_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_INVALID_CREDENTIALS,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from None
    except AuthenticationServiceError:
        record_audit_event(
            db,
            event_type=EVENT_LOGIN_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_AUTHENTICATION_UNAVAILABLE,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        ) from None

    record_login_success(db, user_id=result.user.id)
    _set_session_cookie(response, request, result.raw_token)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    settings = request.app.state.settings
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    raw_token = request.cookies.get(settings.session_cookie_name)
    try:
        revoke_session(db, raw_token)
    except AuthenticationServiceError:
        unavailable = JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Authentication is temporarily unavailable"},
        )
        _clear_session_cookie(unavailable, request)
        return unavailable

    _clear_session_cookie(response, request)
    return response
