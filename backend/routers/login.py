import logging
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

from argon2.exceptions import VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests import RequestException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.redis_session import (
    SESSION_TTL_SECONDS,
    create_session as create_redis_session,
    get_session as get_redis_session,
)
from db.schemas import AccountNameUpdateIn, LoginIn
from db.session import SESSION_COOKIE_SECURE, get_session
from db.tables_phase1 import Account, AccountRole, ph

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
GOOGLE_STATE_COOKIE = "google_oauth_state"
GOOGLE_CODE_VERIFIER_COOKIE = "google_oauth_code_verifier"
GOOGLE_STATE_TTL = 600
DEFAULT_FRONTEND_BASE_URL = "http://localhost:5500"
DEFAULT_GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/google/callback"
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Hash giả: khi email không tồn tại vẫn tốn thời gian verify như bình thường,
# nên không ai đo thời gian phản hồi để dò email nào có trong hệ thống
DUMMY_HASH = ph.hash("dummy-password")


def _google_client_config() -> tuple[dict[str, dict[str, object]], str, str]:
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", DEFAULT_GOOGLE_REDIRECT_URI
    ).strip()
    secrets_file = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "").strip()

    if secrets_file:
        path = Path(secrets_file)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        try:
            credentials = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=503,
                detail="Không thể đọc tệp cấu hình OAuth Google trên máy chủ.",
            ) from exc

        web_config = credentials.get("web") if isinstance(credentials, dict) else None
        if not isinstance(web_config, dict):
            raise HTTPException(
                status_code=503,
                detail="Tệp OAuth Google phải chứa cấu hình client loại Web.",
            )
        client_id = web_config.get("client_id")
        client_secret = web_config.get("client_secret")
        registered_redirect_uris = web_config.get("redirect_uris", [])
        if (
            not isinstance(client_id, str)
            or not client_id.strip()
            or not isinstance(client_secret, str)
            or not client_secret.strip()
            or not isinstance(registered_redirect_uris, list)
            or redirect_uri not in registered_redirect_uris
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Tệp OAuth Google phải có client ID/secret và đăng ký "
                    "đúng GOOGLE_REDIRECT_URI trong redirect_uris."
                ),
            )
        return credentials, client_id.strip(), redirect_uri

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Đăng nhập Google chưa được cấu hình trên máy chủ.",
        )

    return (
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        client_id,
        redirect_uri,
    )


def _create_google_flow(
    *,
    state: str | None = None,
    code_verifier: str | None = None,
) -> Flow:
    client_config, _, redirect_uri = _google_client_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    flow.code_verifier = code_verifier
    return flow


def _frontend_url(page: str, **query: str) -> str:
    base_url = os.getenv("FRONTEND_BASE_URL", DEFAULT_FRONTEND_BASE_URL).rstrip("/")
    query_string = urlencode(query)
    return f"{base_url}/modules/{page}" + (f"?{query_string}" if query_string else "")


def _google_failure_redirect(reason: str) -> RedirectResponse:
    response = RedirectResponse(
        _frontend_url("login.html", google_error=reason),
        status_code=303,
    )
    _clear_google_auth_cookies(response)
    return response


def _clear_google_auth_cookies(response: Response) -> None:
    for cookie_name in (GOOGLE_STATE_COOKIE, GOOGLE_CODE_VERIFIER_COOKIE):
        response.delete_cookie(
            cookie_name,
            path="/auth/google",
            secure=SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="lax",
        )


def _create_session(account: Account) -> str:
    return create_redis_session(account.account_id)


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )


def _account_for_google_identity(
    session: Session,
    *,
    email: str,
    name: str,
) -> Account:
    normalized_email = email.strip().lower()
    account = session.scalar(
        select(Account).where(func.lower(Account.account_email) == normalized_email)
    )
    if account is not None:
        if not account.is_active:
            account.is_active = True
            session.commit()
        return account

    account_name = name.strip()[:100] or normalized_email.split("@", 1)[0][:100]
    account = Account(
        account_name=account_name or "Google User",
        account_email=normalized_email,
        is_active=True,
        role=AccountRole.USER,
        account_code=f"PENDING{secrets.token_hex(6)}",
        password_hash="",
    )
    account.set_password(secrets.token_urlsafe(48))
    session.add(account)

    try:
        session.flush()
        account.account_code = f"ACC{account.account_id:06d}"
        session.commit()
    except IntegrityError:
        session.rollback()
        account = session.scalar(
            select(Account).where(
                func.lower(Account.account_email) == normalized_email
            )
        )
        if account is None:
            raise
        if not account.is_active:
            account.is_active = True
            session.commit()
    return account


def authenticate(session: Session, email: str, raw_password: str) -> Account | None:
    email = email.strip().lower()
    acc = session.scalar(
        select(Account).where(Account.account_email == email)
    )

    if acc is None:
        try:
            ph.verify(DUMMY_HASH, raw_password)
        except VerificationError:
            pass
        return None

    if not acc.verify_password(raw_password):
        return None

    session.commit()  # lưu hash mới nếu verify_password đã tự rehash
    return acc


@router.post(
    "/login",
    responses={401: {"description": "Email hoặc mật khẩu không đúng"}},
)
def login_api(
    data: LoginIn,
    session: Session = Depends(get_session),
):
    # 1. Kiểm tra tài khoản + mật khẩu
    acc = authenticate(
        session,
        data.account_email,
        data.password,
    )

    if acc is None:
        raise HTTPException(
            status_code=401,
            detail="Email hoặc mật khẩu không đúng",
        )

    if not acc.is_active:
        raise HTTPException(
            status_code=403,
            detail="Tài khoản chưa được kích hoạt. Vui lòng xác thực email trước khi đăng nhập.",
        )

    session_id = _create_session(acc)
    response = JSONResponse(
        status_code=200,
        content={
            "message": "Đăng nhập thành công",
            "account_code": acc.account_code,
            "account_name": acc.account_name,
        },
    )
    _set_session_cookie(response, session_id)
    return response


@router.get("/google")
def google_login_start():
    flow = _create_google_flow()
    authorization_url, state = flow.authorization_url(
        access_type="online",
        include_granted_scopes="true",
        prompt="select_account",
    )
    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(
        GOOGLE_STATE_COOKIE,
        state,
        max_age=GOOGLE_STATE_TTL,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/auth/google",
    )
    if flow.code_verifier:
        response.set_cookie(
            GOOGLE_CODE_VERIFIER_COOKIE,
            flow.code_verifier,
            max_age=GOOGLE_STATE_TTL,
            httponly=True,
            secure=SESSION_COOKIE_SECURE,
            samesite="lax",
            path="/auth/google",
        )
    return response


@router.get("/google/callback")
def google_login_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    oauth_state: str | None = Cookie(default=None, alias=GOOGLE_STATE_COOKIE),
    code_verifier: str | None = Cookie(
        default=None,
        alias=GOOGLE_CODE_VERIFIER_COOKIE,
    ),
    session: Session = Depends(get_session),
):
    if error:
        return _google_failure_redirect("cancelled")
    if (
        not code
        or not state
        or not oauth_state
        or not code_verifier
        or not secrets.compare_digest(state, oauth_state)
    ):
        return _google_failure_redirect("invalid_response")

    flow = _create_google_flow(
        state=oauth_state,
        code_verifier=code_verifier,
    )
    try:
        flow.fetch_token(code=code)
    except (GoogleAuthError, OAuth2Error, RequestException, ValueError, Warning) as exc:
        logger.warning(
            "Google OAuth token exchange failed (%s)",
            type(exc).__name__,
        )
        return _google_failure_redirect("token_exchange_failed")

    try:
        _, client_id, _ = _google_client_config()
        claims = id_token.verify_oauth2_token(
            flow.credentials.id_token,
            GoogleRequest(),
            audience=client_id,
        )
    except (GoogleAuthError, OAuth2Error, RequestException, ValueError) as exc:
        logger.warning(
            "Google ID token verification failed (%s)",
            type(exc).__name__,
        )
        return _google_failure_redirect("id_token_invalid")

    email = claims.get("email")
    if (
        not isinstance(email, str)
        or not email.strip()
        or claims.get("email_verified") is not True
    ):
        return _google_failure_redirect("unverified_email")

    name = claims.get("name")
    account = _account_for_google_identity(
        session,
        email=email,
        name=name if isinstance(name, str) else "",
    )
    session_id = _create_session(account)
    response = RedirectResponse(_frontend_url("index.html"), status_code=303)
    _set_session_cookie(response, session_id)
    _clear_google_auth_cookies(response)
    return response


@router.get("/me")
def get_current_account(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    if session_id is None:
        raise HTTPException(
            status_code=401,
            detail="Chưa đăng nhập",
        )

    # 1. Kiểm tra session trong Redis
    account_id = get_redis_session(session_id)

    if account_id is None:
        raise HTTPException(
            status_code=401,
            detail="Session không hợp lệ hoặc đã hết hạn",
        )

    # 2. Lấy thông tin tài khoản từ PostgreSQL
    account = session.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Tài khoản không tồn tại",
        )

    if not account.is_active:
        raise HTTPException(
            status_code=403,
            detail="Tài khoản chưa được kích hoạt.",
        )

    return {
        "account_id": account.account_id,
        "account_name": account.account_name,
        "account_code": account.account_code,
        "account_email": account.account_email,
        "created_at": account.created_at.isoformat(),
        "role": account.role.value,
    }


@router.patch("/me")
def update_current_account(
    data: AccountNameUpdateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    if session_id is None:
        raise HTTPException(
            status_code=401,
            detail="Chưa đăng nhập",
        )

    # 1. Kiểm tra session trong Redis
    account_id = get_redis_session(session_id)

    if account_id is None:
        raise HTTPException(
            status_code=401,
            detail="Session không hợp lệ hoặc đã hết hạn",
        )

    # 2. Lấy thông tin tài khoản từ PostgreSQL
    account = session.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Tài khoản không tồn tại",
        )

    # 3. Kiểm tra tên tài khoản mới
    account_name = data.account_name.strip()

    if len(account_name) < 3:
        raise HTTPException(
            status_code=422,
            detail="Tên tài khoản phải có ít nhất 3 ký tự",
        )

    # 4. Cập nhật tên trong PostgreSQL
    account.account_name = account_name
    session.commit()
    session.refresh(account)

    return {
        "account_id": account.account_id,
        "account_name": account.account_name,
        "account_code": account.account_code,
        "account_email": account.account_email,
        "created_at": account.created_at.isoformat(),
        "role": account.role.value,
    }