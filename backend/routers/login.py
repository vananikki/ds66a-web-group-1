from argon2.exceptions import VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.redis_session import (
    SESSION_TTL_SECONDS,
    create_session,
    get_session as get_redis_session,
)
from db.schemas import AccountNameUpdateIn, LoginIn
from db.session import SESSION_COOKIE_SECURE, get_session
from db.tables_phase1 import Account, ph

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash giả: khi email không tồn tại vẫn tốn thời gian verify như bình thường,
# nên không ai đo thời gian phản hồi để dò email nào có trong hệ thống
DUMMY_HASH = ph.hash("dummy-password")


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

    # 2. Tạo và lưu session vào Redis
    session_id = create_session(acc.account_id)

    # 5. Trả response + gửi session ID cho browser qua cookie
    response = JSONResponse(
        status_code=200,
        content={
            "message": "Đăng nhập thành công",
            "account_code": acc.account_code,
            "account_name": acc.account_name,
        },
    )

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )

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