from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse

from db.redis_session import delete_session
from db.session import SESSION_COOKIE_SECURE

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/logout")
def logout_api(
    session_id: str | None = Cookie(default=None),
):
    # 1. Xóa session trong Redis
    if session_id is not None:
        delete_session(session_id)

    # 2. Trả response
    response = JSONResponse(
        content={"message": "Đăng xuất thành công"},
    )

    # 3. Xóa cookie khỏi trình duyệt
    response.delete_cookie(
        key="session_id",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )

    return response