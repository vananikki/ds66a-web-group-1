from db.session import engine
from db.tables_phase1 import Account, AccountRole, ph
from sqlalchemy.orm import Session
# Thêm import này để SQLAlchemy load đầy đủ registry của Problem, Contest, Submission
import db.tables_phase2  # noqa: F401

email = "admin@example.com"
name = "Super Admin"
password = "12345678"

with Session(engine) as session:
    account = Account(
        account_email=email,
        account_code="admin",
        account_name=name,
        password_hash=ph.hash(password),
        role=AccountRole.SUPER_ADMIN,
    )

    session.add(account)
    session.commit()

    print("Đã tạo tài khoản SUPER_ADMIN.")
    print(f"Email: {email}")
    print(f"Password: {password}")