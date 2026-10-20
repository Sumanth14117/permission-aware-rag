"""HTTP routes for registration, login, and logout."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
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
from app.registration import (
    DuplicateEmailError,
    InvalidEmailError,
    RegistrationError,
    normalize_email,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

MIN_PASSWORD_LENGTH = 8
MAX_ORGANIZATION_NAME_LENGTH = 200


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    organization_name: str
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


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Create an account and try to sign the new user in immediately.

    Returns ``signed_in`` so the client can fall back to the sign-in form when
    the account was created but session creation failed. Passwords, exception
    details, and database identifiers are never returned.
    """
    settings = request.app.state.settings

    organization_name = payload.organization_name.strip()
    if not organization_name or len(organization_name) > MAX_ORGANIZATION_NAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid organization name",
        )
    if (
        len(payload.password) < MIN_PASSWORD_LENGTH
        or not payload.password.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )

    try:
        normalized_email = normalize_email(payload.email)
    except (InvalidEmailError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address",
        ) from None

    try:
        register_user(
            db,
            email=normalized_email,
            password=payload.password,
            organization_name=organization_name,
        )
    except InvalidEmailError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address",
        ) from None
    except DuplicateEmailError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid password",
        ) from None
    except (RegistrationError, SQLAlchemyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is temporarily unavailable",
        ) from None

    try:
        login_result = authenticate_and_create_session(
            db,
            email=normalized_email,
            password=payload.password,
            session_ttl=settings.session_ttl,
        )
    except (AuthenticationError, AuthenticationServiceError):
        # The account exists; let the client sign in explicitly instead of
        # reporting a failure that would make a retry look like a duplicate.
        return {"signed_in": False}

    record_login_success(db, user_id=login_result.user.id)
    _set_session_cookie(response, request, login_result.raw_token)
    response.status_code = status.HTTP_201_CREATED
    return {"signed_in": True}


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
