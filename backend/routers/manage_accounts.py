from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from db.schemas import AccountRoleUpdateIn
from db.redis_session import (
    get_session as get_redis_session,
    delete_account_sessions,
)
from db.session import get_session
from db.tables_phase1 import (
    Account,
    AccountRole,
    Admin,
    ManageClass,
    StudentClass,
)
from db.tables_phase2 import Contest, Problem, Submission

router = APIRouter(prefix="/admin/accounts", tags=["account management"])


def get_super_admin(
    session_id: str | None,
    session: Session,
) -> Account:
    # 1. Kiểm tra đã đăng nhập chưa
    if session_id is None:
        raise HTTPException(
            status_code=401,
            detail="Chưa đăng nhập",
        )

    # 2. Kiểm tra session trong Redis
    account_id = get_redis_session(session_id)

    if account_id is None:
        raise HTTPException(
            status_code=401,
            detail="Session không hợp lệ hoặc đã hết hạn",
        )

    # 3. Lấy thông tin account từ PostgreSQL
    account = session.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Tài khoản không tồn tại",
        )

    # 4. Kiểm tra quyền Super Admin
    if not account.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Không có quyền quản lý tài khoản",
        )

    return account


def get_authenticated_account(
    session_id: str | None,
    session: Session,
) -> Account:
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

    # 2. Lấy thông tin account từ PostgreSQL
    account = session.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Tài khoản không tồn tại",
        )

    return account


@router.get("")
def list_accounts(
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    get_super_admin(session_id, session)
    accounts = session.scalars(
        select(Account)
        .options(selectinload(Account.admin))
        .order_by(Account.account_id)
    ).all()

    return [
        {
            "account_id": account.account_id,
            "account_code": account.account_code,
            "account_name": account.account_name,
            "account_email": account.account_email,
            "role": account.role.value,
            "admin_active": bool(account.admin and account.admin.is_active),
        }
        for account in accounts
    ]


@router.patch("/{account_id}/role")
def update_account_role(
    account_id: int,
    data: AccountRoleUpdateIn,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    acting_account = get_super_admin(session_id, session)
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại")

    if account.account_id == acting_account.account_id and data.role != AccountRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=409,
            detail="Không thể tự hạ quyền super admin",
        )

    if account.is_super_admin and data.role != AccountRole.SUPER_ADMIN:
        super_admin_count = session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.role == AccountRole.SUPER_ADMIN)
        )
        if super_admin_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Không thể hạ quyền super admin cuối cùng",
            )

    admin = session.scalar(
        select(Admin).where(Admin.account_id == account.account_id)
    )
    should_be_admin = data.role in {
        AccountRole.ADMIN,
        AccountRole.SUPER_ADMIN,
    }
    if should_be_admin and admin is None:
        admin = Admin(account_id=account.account_id, is_active=True)
        session.add(admin)
    elif admin is not None:
        admin.is_active = should_be_admin

    account.role = data.role
    session.commit()
    session.refresh(account)

    return {
        "account_id": account.account_id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "account_email": account.account_email,
        "role": account.role.value,
        "admin_active": bool(admin and admin.is_active),
    }


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    session_id: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    acting_account = get_authenticated_account(session_id, session)
    target_account = session.get(Account, account_id)
    if target_account is None:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại")

    if target_account.account_id == acting_account.account_id:
        raise HTTPException(
            status_code=403,
            detail="Không thể tự xóa tài khoản của mình",
        )

    if acting_account.is_super_admin:
        if target_account.is_super_admin:
            super_admin_count = session.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.role == AccountRole.SUPER_ADMIN)
            )
            if super_admin_count <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Không thể xóa super admin cuối cùng",
                )
    else:
        if target_account.account_id != acting_account.account_id:
            raise HTTPException(
                status_code=403,
                detail="Bạn chỉ có thể xóa tài khoản của chính mình",
            )

    if target_account.admin is not None:
        session.execute(
            delete(ManageClass).where(
                ManageClass.admin_id == target_account.admin.admin_id,
            )
        )
        session.delete(target_account.admin)

    if target_account.student is not None:
        session.execute(
            delete(StudentClass).where(
                StudentClass.student_id == target_account.student.student_id,
            )
        )
        session.delete(target_account.student)

    session.execute(
        update(Problem)
        .where(Problem.created_by == target_account.account_id)
        .values(created_by=None)
    )
    session.execute(
        update(Contest)
        .where(Contest.created_by == target_account.account_id)
        .values(created_by=None)
    )
    session.execute(
        update(Submission)
        .where(Submission.account_id == target_account.account_id)
        .values(account_id=None)
    )
    session.delete(target_account)
    session.commit()

    # Thu hồi toàn bộ Redis Session sau khi xóa account thành công
    delete_account_sessions(account_id)

    return {
        "message": "Xóa tài khoản thành công",
        "deleted_account_id": account_id,
    }
