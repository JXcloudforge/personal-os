from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.auth.security import create_session, get_session, hash_password
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AuthSecurityState, Invitation, SessionToken, User
from app.services import registration_service
from app.services.invitation_service import create_invitation


@pytest.fixture
def registration_environment(tmp_path, monkeypatch):
    db_path = tmp_path / "registration.sqlite3"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(registration_service, "SessionLocal", factory)
    monkeypatch.setattr(settings, "secret_key", "registration-test-secret-key-at-least-32-bytes")
    monkeypatch.setattr(settings, "allowed_origins", "")

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield factory, engine
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _invite(factory, *, expires_at=None, revoked=False, claimed=False):
    with factory() as db:
        token, row = create_invitation(db, ttl=timedelta(hours=1))
        if expires_at is not None:
            row.expires_at = expires_at
        if revoked:
            row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        if claimed:
            user = User(username=f"already-{row.id}", password_hash="synthetic")
            db.add(user)
            db.flush()
            row.claimed_at = datetime.now(UTC).replace(tzinfo=None)
            row.claimed_by_user_id = user.id
        db.commit()
        return token, row.id


def _challenge(client):
    response = client.get("/api/auth/registration/challenge", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    return response.json()["challenge"]


def _register(client, *, code, username, password="Secure-Password-2026!", challenge=None, origin="http://testserver"):
    token = challenge if challenge is not None else _challenge(client)
    return client.post(
        "/api/auth/register",
        headers={"Origin": origin, "X-Registration-CSRF": token},
        json={"invitation_code": code, "username": username, "password": password},
    )


def test_invitation_registration_commits_user_invite_and_session_then_allows_login_recovery(registration_environment):
    factory, _engine = registration_environment
    code, invite_id = _invite(factory)
    client = TestClient(app)
    try:
        response = _register(client, code=code, username="New.User")
        assert response.status_code == 201
        assert response.json() == {"username": "new.user"}
        assert "personal_os_session" in response.cookies
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/auth/me").status_code == 200
        with factory() as db:
            user = db.scalar(select(User).where(User.username == "new.user"))
            invitation = db.get(Invitation, invite_id)
            assert user and invitation and invitation.claimed_by_user_id == user.id
            assert db.scalar(select(func_count_sessions(user.id))) == 1
            assert invitation.claimed_at is not None

        # If the registration response is lost after commit, normal login recovers access.
        recovery_client = TestClient(app)
        login = recovery_client.post("/api/auth/login", json={"username": "new.user", "password": "Secure-Password-2026!"})
        assert login.status_code == 200
        assert recovery_client.get("/api/auth/me").status_code == 200
        retry_client = TestClient(app)
        retry = _register(retry_client, code=code, username="second.user")
        assert retry.status_code == 400
        assert retry.json()["detail"] == "Registration could not be completed"
        with factory() as db:
            assert db.scalar(select(func_count_users_with_prefix("second.user"))) == 0
    finally:
        client.close()


def func_count_sessions(user_id):
    from sqlalchemy import func
    return select(func.count(SessionToken.id)).where(SessionToken.user_id == user_id).scalar_subquery()


def func_count_users_with_prefix(username):
    from sqlalchemy import func
    return select(func.count(User.id)).where(User.username == username).scalar_subquery()


@pytest.mark.parametrize("state", ["invalid", "expired", "revoked", "claimed"])
def test_unavailable_invitation_states_have_one_public_error(registration_environment, state):
    factory, _ = registration_environment
    if state == "invalid":
        code = "pos_inv_not-a-real-registration-invitation"
    elif state == "expired":
        code, _ = _invite(factory, expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
    else:
        code, _ = _invite(factory, revoked=state == "revoked", claimed=state == "claimed")
    client = TestClient(app)
    try:
        response = _register(client, code=code, username=f"candidate-{state}")
        assert response.status_code == 400
        assert response.json()["detail"] == "Registration could not be completed"
        assert "personal_os_session" not in response.cookies
        with factory() as db:
            assert db.scalar(select(func_count_users_with_prefix(f"candidate-{state}"))) == 0
            # The limiter transaction is independent from the failed registration transaction.
            identity_hash = registration_service._state_hash("registration-identity", f"testclient\0candidate-{state}")
            assert db.get(AuthSecurityState, identity_hash).attempt_count == 1
    finally:
        client.close()


def test_registration_requires_same_origin_and_one_time_csrf_challenge(registration_environment):
    factory, _ = registration_environment
    code, _ = _invite(factory)
    client = TestClient(app)
    try:
        challenge = _challenge(client)
        bad_origin = _register(client, code=code, username="bad-origin", challenge=challenge, origin="https://attacker.example")
        assert bad_origin.status_code == 403

        challenge = _challenge(client)
        missing_origin = client.post("/api/auth/register", headers={"X-Registration-CSRF": challenge}, json={"invitation_code": code, "username": "no-origin", "password": "Secure-Password-2026!"})
        assert missing_origin.status_code == 403

        # The previously issued challenge is consumed by a failed attempt; replay is rejected.
        challenge = _challenge(client)
        failed = _register(client, code="pos_inv_invalid-but-long-enough-to-validate", username="replay-test", challenge=challenge)
        assert failed.status_code == 400
        replay = client.post("/api/auth/register", headers={"Origin": "http://testserver", "X-Registration-CSRF": challenge}, json={"invitation_code": code, "username": "replay-test-2", "password": "Secure-Password-2026!"})
        assert replay.status_code == 403
    finally:
        client.close()


def test_same_invitation_concurrent_registration_creates_only_one_account(registration_environment):
    factory, _ = registration_environment
    code, invite_id = _invite(factory)

    def perform(username):
        client = TestClient(app)
        challenge = _challenge(client)
        return _register(client, code=code, username=username, challenge=challenge).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(perform, ["parallel-a", "parallel-b"]))
    assert sorted(statuses) == [201, 400]
    with factory() as db:
        invite = db.get(Invitation, invite_id)
        assert invite and invite.claimed_by_user_id is not None
        assert db.scalar(select(func_count_sessions(invite.claimed_by_user_id))) == 1
        assert db.scalar(select(func_count_users_with_prefix("parallel-a"))) + db.scalar(select(func_count_users_with_prefix("parallel-b"))) == 1


def test_user_or_session_write_failure_rolls_back_invitation_and_all_registration_rows(registration_environment, monkeypatch):
    factory, _ = registration_environment
    code, invite_id = _invite(factory)
    original_claim = registration_service.claim_invitation

    def claim_then_fail(db, token, user_id):
        original_claim(db, token, user_id)
        raise RuntimeError("synthetic user-adjacent write failure")

    monkeypatch.setattr(registration_service, "claim_invitation", claim_then_fail)
    with factory() as db:
        with pytest.raises(RuntimeError):
            registration_service.register_with_invitation(db, invitation_code=code, username="rollback-user", password="Secure-Password-2026!")
    with factory() as db:
        invite = db.get(Invitation, invite_id)
        assert invite and invite.claimed_at is None and invite.claimed_by_user_id is None
        assert db.scalar(select(func_count_users_with_prefix("rollback-user"))) == 0
        assert db.scalar(select(func_count_sessions(0))) == 0

    monkeypatch.setattr(registration_service, "claim_invitation", original_claim)
    original_session = registration_service.create_session_uncommitted

    def fail_session(*_args, **_kwargs):
        raise RuntimeError("synthetic session insert failure")

    monkeypatch.setattr(registration_service, "create_session_uncommitted", fail_session)
    with factory() as db:
        with pytest.raises(RuntimeError):
            registration_service.register_with_invitation(db, invitation_code=code, username="session-rollback", password="Secure-Password-2026!")
    with factory() as db:
        invite = db.get(Invitation, invite_id)
        assert invite and invite.claimed_at is None
        assert db.scalar(select(func_count_users_with_prefix("session-rollback"))) == 0
        assert db.scalar(select(func_count_sessions(0))) == 0
    monkeypatch.setattr(registration_service, "create_session_uncommitted", original_session)


def test_username_conflict_does_not_consume_valid_invitation(registration_environment):
    factory, _ = registration_environment
    code, invite_id = _invite(factory)
    with factory() as db:
        db.add(User(username="existing-user", password_hash="synthetic-hash"))
        db.commit()
    client = TestClient(app)
    try:
        response = _register(client, code=code, username="EXISTING-USER")
        assert response.status_code == 400
        assert response.json()["detail"] == "Registration could not be completed"
        with factory() as db:
            invite = db.get(Invitation, invite_id)
            assert invite and invite.claimed_at is None
            assert db.scalar(select(func_count_users_with_prefix("existing-user"))) == 1
    finally:
        client.close()


def test_session_failure_returns_no_auth_cookie_and_keeps_invitation_reusable(registration_environment, monkeypatch):
    factory, _ = registration_environment
    code, invite_id = _invite(factory)

    def fail_session(*_args, **_kwargs):
        raise RuntimeError("synthetic session insert failure")

    monkeypatch.setattr(registration_service, "create_session_uncommitted", fail_session)
    client = TestClient(app, raise_server_exceptions=False)
    response = _register(client, code=code, username="session-failure")
    assert response.status_code == 500
    assert "personal_os_session" not in response.cookies
    with factory() as db:
        invite = db.get(Invitation, invite_id)
        assert invite and invite.claimed_at is None
        assert db.scalar(select(func_count_users_with_prefix("session-failure"))) == 0



def test_registration_limits_survive_new_session_factory(registration_environment, monkeypatch):
    factory, engine = registration_environment
    monkeypatch.setattr(registration_service, "REGISTRATION_IDENTITY_LIMIT", 1)
    for index in range(2):
        challenge = registration_service.issue_registration_challenge(source_ip="203.0.113.45")
        result = registration_service.record_registration_attempt(source_ip="203.0.113.45", username="limited-user", challenge=challenge)
        if index == 0:
            assert result.csrf_valid and not result.rate_limited
        else:
            assert result.csrf_valid and result.rate_limited

    # A new independent SessionLocal equivalent sees the committed bucket.
    another_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    old_factory = registration_service.SessionLocal
    monkeypatch.setattr(registration_service, "SessionLocal", another_factory)
    challenge = registration_service.issue_registration_challenge(source_ip="203.0.113.45")
    result = registration_service.record_registration_attempt(source_ip="203.0.113.45", username="limited-user", challenge=challenge)
    assert result.rate_limited
    monkeypatch.setattr(registration_service, "SessionLocal", old_factory)


def test_registration_security_storage_failure_is_fail_closed(registration_environment, monkeypatch):
    def unavailable_factory():
        raise OperationalError("registration security state", {}, RuntimeError("offline"))

    monkeypatch.setattr(registration_service, "SessionLocal", unavailable_factory)
    with pytest.raises(registration_service.RegistrationSecurityUnavailable):
        registration_service.issue_registration_challenge(source_ip="203.0.113.88")


def test_registration_source_address_ignores_untrusted_forwarding_headers():
    from starlette.requests import Request
    from app.api.routes import _request_source_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"198.51.100.222")],
        "client": ("127.0.0.1", 45123),
        "scheme": "http",
        "server": ("testserver", 80),
        "method": "POST",
        "path": "/api/auth/register",
        "query_string": b"",
    }
    assert _request_source_ip(Request(scope)) == "127.0.0.1"


def test_blocked_ip_does_not_create_unbounded_username_bucket_rows(registration_environment, monkeypatch):
    factory, _ = registration_environment
    monkeypatch.setattr(registration_service, "REGISTRATION_IP_LIMIT", 1)
    for username in ("bounded-one", "attacker-chosen-two", "attacker-chosen-three"):
        challenge = registration_service.issue_registration_challenge(source_ip="203.0.113.77")
        result = registration_service.record_registration_attempt(source_ip="203.0.113.77", username=username, challenge=challenge)
        assert result.rate_limited is (username != "bounded-one")
    with factory() as db:
        identity_rows = db.scalars(select(AuthSecurityState).where(AuthSecurityState.state_kind == "rate_limit")).all()
        hashes = {row.state_hash for row in identity_rows}
        # Only the allowed request created a username bucket; blocked requests still charge the IP bucket.
        assert registration_service._state_hash("registration-identity", "203.0.113.77\0bounded-one") in hashes
        assert registration_service._state_hash("registration-identity", "203.0.113.77\0attacker-chosen-two") not in hashes
        assert registration_service._state_hash("registration-identity", "203.0.113.77\0attacker-chosen-three") not in hashes


def test_identity_bucket_is_scoped_to_the_source_not_globally_lockable(registration_environment, monkeypatch):
    monkeypatch.setattr(registration_service, "REGISTRATION_IDENTITY_LIMIT", 1)
    outcomes = []
    for source in ("198.51.100.10", "198.51.100.10", "198.51.100.11"):
        challenge = registration_service.issue_registration_challenge(source_ip=source)
        result = registration_service.record_registration_attempt(source_ip=source, username="same-name", challenge=challenge)
        assert result.csrf_valid
        outcomes.append(result.rate_limited)
    assert outcomes == [False, True, False]


def test_cli_only_invitation_operations_are_not_exposed_as_web_routes(registration_environment):
    _factory, _ = registration_environment
    paths = set(app.openapi()["paths"])
    assert "/api/auth/register" in paths
    assert not any("invitation" in path or "invite" in path for path in paths)
    anonymous = TestClient(app)
    assert anonymous.get("/api/data/backups").status_code == 401
    anonymous.close()


def test_legacy_session_creation_keeps_self_committing_contract(registration_environment):
    factory, _ = registration_environment
    with factory() as db:
        user = User(username="legacy-session-user", password_hash=hash_password("Old-Account-Password-2026!"))
        db.add(user)
        db.commit()
        raw, _csrf = create_session(db, user)
        assert get_session(db, raw) is not None
    with factory() as db:
        assert db.scalar(select(SessionToken.id).where(SessionToken.user_id == user.id)) is not None


def test_security_state_migration_upgrades_fresh_and_synthetic_legacy_databases(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "migration-empty.sqlite3"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        assert {column["name"] for column in inspect(engine).get_columns("auth_security_state")} == {
            "state_hash", "state_kind", "window_started_at", "attempt_count", "expires_at", "consumed_at"
        }
    finally:
        engine.dispose()

    legacy = tmp_path / "migration-v04b1-copy.sqlite3"
    legacy_config = Config(str(backend / "alembic.ini"))
    legacy_config.set_main_option("sqlalchemy.url", f"sqlite:///{legacy.as_posix()}")
    command.upgrade(legacy_config, "f4b5c6d7e8f9")
    connection = sqlite3.connect(legacy)
    connection.execute("INSERT INTO users (username,password_hash,is_active) VALUES ('legacy-user','synthetic-hash',1)")
    user_id = connection.execute("SELECT id FROM users WHERE username='legacy-user'").fetchone()[0]
    connection.execute("INSERT INTO sessions (user_id,token_hash,csrf_token,expires_at) VALUES (?,?,?,?)", (user_id, "legacy-session-hash", "legacy-csrf", "2099-01-01"))
    connection.commit()
    connection.close()
    command.upgrade(legacy_config, "head")
    connection = sqlite3.connect(f"file:{legacy.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT username,password_hash,is_active FROM users WHERE id=?", (user_id,)).fetchone() == ("legacy-user", "synthetic-hash", 1)
        assert connection.execute("SELECT token_hash,csrf_token FROM sessions WHERE user_id=?", (user_id,)).fetchone() == ("legacy-session-hash", "legacy-csrf")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        connection.close()
