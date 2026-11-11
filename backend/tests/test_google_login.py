from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import routers.login as login
import db.redis_session as redis_session
from db.session import get_session
from db.tables_phase1 import (
    Account,
    AccountRole,
    Base,
)
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
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(
        redis_session,
        "redis_client",
        fakeredis.FakeRedis(decode_responses=True),
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield TestClient(app)
    finally:
        Base.metadata.drop_all(bind=engine)
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


def test_frontend_login_page_is_served_by_backend(client):
    response = client.get("/modules/login.html")

    assert response.status_code == 200
    assert 'id="btn-google-login"' in response.text


class FakeGoogleFlow:
    def __init__(self, state=None, code_verifier=None):
        self.state = state
        self.code_verifier = code_verifier
        self.credentials = SimpleNamespace(id_token="verified-id-token")

    def authorization_url(self, **kwargs):
        self.state = "oauth-state"
        self.code_verifier = "test-pkce-code-verifier"
        return "https://accounts.google.test/authorize", self.state

    def fetch_token(self, *, code):
        assert code == "authorization-code"
        assert self.code_verifier == "test-pkce-code-verifier"


def configure_google(monkeypatch, claims):
    monkeypatch.setenv("FRONTEND_BASE_URL", "http://localhost:5500")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(login, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(
        login,
        "_create_google_flow",
        lambda *, state=None, code_verifier=None: FakeGoogleFlow(
            state=state,
            code_verifier=code_verifier,
        ),
    )
    monkeypatch.setattr(
        login.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: claims,
    )


def test_google_web_client_file_requires_exact_callback_uri(monkeypatch, tmp_path):
    secrets_file = tmp_path / "client_secret.json"
    secrets_file.write_text(
        (
            '{"web":{"client_id":"web-client-id",'
            '"client_secret":"web-client-secret",'
            '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
            '"token_uri":"https://oauth2.googleapis.com/token",'
            '"redirect_uris":["http://localhost:8000"]}}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/auth/google/callback",
    )

    with pytest.raises(HTTPException) as exc_info:
        login._google_client_config()

    assert exc_info.value.status_code == 503
    assert "đăng ký đúng GOOGLE_REDIRECT_URI" in exc_info.value.detail


def test_google_web_client_file_loads_when_callback_matches(monkeypatch, tmp_path):
    callback_uri = "http://localhost:8000/auth/google/callback"
    secrets_file = tmp_path / "client_secret.json"
    secrets_file.write_text(
        (
            '{"web":{"client_id":"web-client-id",'
            '"client_secret":"web-client-secret",'
            '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
            '"token_uri":"https://oauth2.googleapis.com/token",'
            f'"redirect_uris":["{callback_uri}"]}}}}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", callback_uri)

    client_config, client_id, redirect_uri = login._google_client_config()

    assert client_config["web"]["client_id"] == "web-client-id"
    assert client_id == "web-client-id"
    assert redirect_uri == callback_uri


def test_google_login_requires_server_credentials(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    response = client.get("/auth/google")

    assert response.status_code == 503
    assert response.json()["detail"] == "Đăng nhập Google chưa được cấu hình trên máy chủ."


def test_google_oauth_requests_canonical_userinfo_scopes():
    assert login.GOOGLE_SCOPES == [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]


def test_google_login_creates_account_and_session(client, monkeypatch):
    configure_google(
        monkeypatch,
        {
            "email": "New.User@example.com",
            "email_verified": True,
            "name": "New Google User",
        },
    )

    start = client.get("/auth/google", follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"] == "https://accounts.google.test/authorize"
    assert "httponly" in start.headers["set-cookie"].lower()
    assert "google_oauth_code_verifier=" in start.headers["set-cookie"]

    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=oauth-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert callback.headers["location"] == "http://localhost:5500/modules/index.html"
    assert "session_id=" in callback.headers["set-cookie"]
    assert "google_oauth_state=" in callback.headers["set-cookie"]
    assert "google_oauth_code_verifier=" in callback.headers["set-cookie"]

    with TestingSessionLocal() as db:
        account = db.query(Account).one()
        assert account.account_email == "new.user@example.com"
        assert account.account_name == "New Google User"
        assert account.account_code.startswith("ACC")
        assert account.role is AccountRole.USER
        assert account.is_active is True
    session_id = client.cookies.get("session_id")
    assert redis_session.get_session(session_id) == account.account_id

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["account_email"] == "new.user@example.com"


def test_google_login_links_verified_email_without_changing_existing_role(
    client,
    monkeypatch,
):
    with TestingSessionLocal() as db:
        account = Account(
            account_email="teacher@example.com",
            account_code="TEACHER1",
            account_name="Existing Teacher",
            role=AccountRole.ADMIN,
            is_active=False,
        )
        account.set_password("StrongPassword123")
        db.add(account)
        db.commit()
        account_id = account.account_id

    configure_google(
        monkeypatch,
        {
            "email": "TEACHER@example.com",
            "email_verified": True,
            "name": "Google Display Name",
        },
    )
    client.get("/auth/google", follow_redirects=False)
    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=oauth-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    with TestingSessionLocal() as db:
        account = db.get(Account, account_id)
        assert db.query(Account).count() == 1
        assert account.account_name == "Existing Teacher"
        assert account.role is AccountRole.ADMIN
        assert account.is_active is True
    session_id = client.cookies.get("session_id")
    assert redis_session.get_session(session_id) == account_id


def test_google_login_rejects_unverified_email_without_creating_session(
    client,
    monkeypatch,
):
    configure_google(
        monkeypatch,
        {
            "email": "unverified@example.com",
            "email_verified": False,
            "name": "Unverified User",
        },
    )
    client.get("/auth/google", follow_redirects=False)
    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=oauth-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert "google_error=unverified_email" in callback.headers["location"]
    assert "session_id=" not in callback.headers.get("set-cookie", "")
    with TestingSessionLocal() as db:
        assert db.query(Account).count() == 0
    assert redis_session.redis_client.keys("session:*") == []


def test_google_login_rejects_mismatched_state(client, monkeypatch):
    configure_google(
        monkeypatch,
        {
            "email": "state@example.com",
            "email_verified": True,
            "name": "State User",
        },
    )
    client.get("/auth/google", follow_redirects=False)
    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=wrong-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert "google_error=invalid_response" in callback.headers["location"]
    with TestingSessionLocal() as db:
        assert db.query(Account).count() == 0
    assert redis_session.redis_client.keys("session:*") == []


def test_google_login_reports_id_token_verification_failure(client, monkeypatch):
    configure_google(
        monkeypatch,
        {
            "email": "invalid-token@example.com",
            "email_verified": True,
            "name": "Invalid Token User",
        },
    )
    monkeypatch.setattr(
        login.id_token,
        "verify_oauth2_token",
        Mock(side_effect=ValueError("invalid token")),
    )
    client.get("/auth/google", follow_redirects=False)

    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=oauth-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert "google_error=id_token_invalid" in callback.headers["location"]


def test_google_login_redirects_if_oauth_library_rejects_scope_change(
    client,
    monkeypatch,
):
    configure_google(
        monkeypatch,
        {
            "email": "scope-warning@example.com",
            "email_verified": True,
            "name": "Scope Warning User",
        },
    )
    flow = FakeGoogleFlow(state="oauth-state", code_verifier="test-pkce-code-verifier")
    flow.fetch_token = Mock(side_effect=Warning("scope changed"))
    monkeypatch.setattr(
        login,
        "_create_google_flow",
        lambda *, state=None, code_verifier=None: flow,
    )
    client.get("/auth/google", follow_redirects=False)

    callback = client.get(
        "/auth/google/callback?code=authorization-code&state=oauth-state",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert "google_error=token_exchange_failed" in callback.headers["location"]
