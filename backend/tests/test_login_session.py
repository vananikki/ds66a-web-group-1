import pytest
import fakeredis
import db.redis_session as redis_session
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from db.redis_session import SESSION_TTL_SECONDS

from db.session import (
    SESSION_COOKIE_SECURE,
    get_session,
)
from db.tables_phase1 import Account, AccountRole, Base
from src.app import app

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(monkeypatch):
    # 1. Tạo Redis giả lập cho từng bài test
    fake_redis = fakeredis.FakeRedis(decode_responses=True)

    # 2. Thay Redis thật bằng Redis giả lập
    monkeypatch.setattr(
        redis_session,
        "redis_client",
        fake_redis,
    )

    # 3. Giữ nguyên phần SQLite test database
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_get_session

    Base.metadata.create_all(bind=engine)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        Base.metadata.drop_all(bind=engine)

        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


def create_account(*, is_active=True):
    with TestingSessionLocal() as db:
        account = Account(
            account_email="login@example.com",
            account_code="LOGIN1",
            account_name="Login User",
            role=AccountRole.USER,
            is_active=is_active,
        )
        account.set_password("StrongPassword123")
        db.add(account)
        db.commit()
        db.refresh(account)
        return account.account_id



def test_login_sets_http_only_cookie_with_matching_session_expiry(client):
    account_id = create_account()

    # 1. Đăng nhập
    response = client.post(
        "/auth/login",
        json={
            "account_email": "login@example.com",
            "password": "StrongPassword123",
        },
    )

    assert response.status_code == 200, response.text

    # 2. Kiểm tra cookie
    cookie = response.headers["set-cookie"].lower()

    assert "session_id=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"max-age={SESSION_TTL_SECONDS}" in cookie
    assert ("secure" in cookie) is SESSION_COOKIE_SECURE

    # 3. Lấy session_id từ cookie
    session_id = client.cookies.get("session_id")

    assert session_id is not None

    # 4. Kiểm tra session trong Redis
    stored_account_id = redis_session.get_session(session_id)

    assert stored_account_id == account_id

    # 5. Kiểm tra TTL
    session_key = f"session:{session_id}"

    ttl = redis_session.redis_client.ttl(session_key)

    assert 0 < ttl <= SESSION_TTL_SECONDS

    # 6. Kiểm tra endpoint /auth/me
    me_response = client.get("/auth/me")

    assert me_response.status_code == 200, me_response.text
    assert me_response.json()["account_id"] == account_id

    # 7. Đăng xuất
    logout_response = client.post("/auth/logout")

    assert logout_response.status_code == 200
    assert "max-age=0" in logout_response.headers["set-cookie"].lower()

    # 8. Session phải bị xóa khỏi Redis
    assert redis_session.get_session(session_id) is None

    # 9. Không thể truy cập /auth/me sau logout
    assert client.get("/auth/me").status_code == 401


def test_login_rejects_invalid_password_without_setting_session_cookie(client):
    create_account()

    response = client.post(
        "/auth/login",
        json={
            "account_email": "login@example.com",
            "password": "WrongPassword123",
        },
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.get("/auth/me").status_code == 401


def test_redis_session_expires_automatically(client):
    account_id = create_account()

    # 1. Tạo session trong Redis giả lập
    session_id = redis_session.create_session(account_id)

    # 2. Kiểm tra session tồn tại
    assert redis_session.get_session(session_id) == account_id

    # 3. Giả lập session hết hạn
    session_key = f"session:{session_id}"

    redis_session.redis_client.delete(session_key)

    # 4. Kiểm tra session không còn hợp lệ
    assert redis_session.get_session(session_id) is None

    # 5. Thử truy cập /auth/me bằng session đã bị xóa
    client.cookies.set("session_id", session_id)

    response = client.get("/auth/me")

    assert response.status_code == 401



def test_unverified_email_requires_correct_password_and_never_creates_session(client):
    create_account(is_active=False)

    invalid_password_response = client.post(
        "/auth/login",
        json={
            "account_email": "login@example.com",
            "password": "WrongPassword123",
        },
    )
    assert invalid_password_response.status_code == 401
    assert invalid_password_response.json()["detail"] == "Email hoặc mật khẩu không đúng"
    assert "set-cookie" not in invalid_password_response.headers

    correct_password_response = client.post(
        "/auth/login",
        json={
            "account_email": "login@example.com",
            "password": "StrongPassword123",
        },
    )
    assert correct_password_response.status_code == 403
    assert "chưa được kích hoạt" in correct_password_response.json()["detail"]
    assert "set-cookie" not in correct_password_response.headers

    assert redis_session.redis_client.keys("session:*") == []
