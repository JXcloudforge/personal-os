from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import hashlib

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Invitation, SessionToken, User
from app.services.invitation_service import (
    InvitationUnavailable,
    claim_invitation,
    create_invitation,
    get_invitation_metadata,
    get_invitation_state,
    list_invitation_metadata,
    revoke_invitation,
)


@pytest.fixture
def invitation_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'invites.sqlite3').as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        first = User(username="invite-a", password_hash="test-only-hash")
        second = User(username="invite-b", password_hash="test-only-hash")
        db.add_all([first, second])
        db.flush()
        db.add(SessionToken(user_id=first.id, token_hash="a" * 64, csrf_token="synthetic", expires_at=datetime(2099, 1, 1)))
        db.commit()
        ids = first.id, second.id
    yield factory, ids
    engine.dispose()


def test_invitation_hash_metadata_claim_expiry_revoke_and_single_use(invitation_db):
    factory, (user_a, user_b) = invitation_db
    with factory() as db:
        token, row = create_invitation(db, ttl=timedelta(hours=1))
        db.commit()
        assert token and len(token) >= 40
        assert row.token_hash != token
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert get_invitation_state(row) == "OPEN"
        metadata = list_invitation_metadata(db)
        assert len(metadata) == 1 and not hasattr(metadata[0], "token")
        claim_invitation(db, token, user_a)
        db.commit()
        assert get_invitation_state(row) == "CLAIMED"
        with pytest.raises(InvitationUnavailable):
            claim_invitation(db, token, user_b)

        expired_at = datetime(2000, 1, 1)
        expiring, expired_row = create_invitation(db, ttl=timedelta(seconds=1), now=expired_at)
        db.commit()
        assert get_invitation_state(expired_row, now=expired_at + timedelta(seconds=2)) == "EXPIRED"
        with pytest.raises(InvitationUnavailable):
            claim_invitation(db, expiring, user_b, now=expired_at + timedelta(seconds=2))

        revocable, revoked_row = create_invitation(db, ttl=timedelta(hours=1))
        db.commit()
        assert revoke_invitation(db, revoked_row.id)
        db.commit()
        assert get_invitation_metadata(db, revoked_row.id).state == "REVOKED"
        with pytest.raises(InvitationUnavailable):
            claim_invitation(db, revocable, user_b)

        duplicate = Invitation(token_hash=row.token_hash, expires_at=datetime(2099, 1, 1))
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_concurrent_claims_allow_exactly_one_and_outer_rollback_restores_open_invite(invitation_db):
    factory, (user_a, user_b) = invitation_db
    with factory() as db:
        token, row = create_invitation(db, ttl=timedelta(hours=1))
        db.commit()
        invite_id = row.id

    def claim(user_id):
        with factory() as db:
            try:
                claim_invitation(db, token, user_id)
                db.commit()
                return True
            except InvitationUnavailable:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [user_a, user_b]))
    assert sorted(outcomes) == [False, True]
    with factory() as db:
        persisted = db.get(Invitation, invite_id)
        assert persisted.claimed_by_user_id in {user_a, user_b}

    with factory() as db:
        token2, row2 = create_invitation(db, ttl=timedelta(hours=1))
        db.commit()
        invite_id2 = row2.id
    with factory() as db:
        claim_invitation(db, token2, user_a)
        db.add(User(username="invite-a", password_hash="synthetic-duplicate"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with factory() as db:
        restored = db.get(Invitation, invite_id2)
        assert restored.claimed_at is None and restored.claimed_by_user_id is None


def test_invitation_cli_only_discloses_new_token_and_revoke_list_hide_it(invitation_db, monkeypatch, capsys):
    from scripts import manage_invites

    factory, _ = invitation_db
    monkeypatch.setattr(manage_invites, "SessionLocal", factory)
    monkeypatch.setattr(manage_invites, "_is_interactive_terminal", lambda: True)
    assert manage_invites.main(["create", "--ttl-hours", "24"]) == 0
    created_output = capsys.readouterr().out
    token_line = next(line for line in created_output.splitlines() if line.startswith("Invite code (shown once): "))
    token = token_line.split(": ", 1)[1]
    assert token
    assert manage_invites.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert token not in listing
    assert "OPEN" in listing
    assert manage_invites.main(["revoke", "1"]) == 0
    assert token not in capsys.readouterr().out
    assert manage_invites.main(["show", "1"]) == 0
    assert "REVOKED" in capsys.readouterr().out


def test_invitation_cli_refuses_to_create_when_output_is_redirected(invitation_db, monkeypatch, capsys):
    from scripts import manage_invites

    factory, _ = invitation_db

    def forbidden_factory():
        raise AssertionError("database must not be opened when one-time output is redirected")

    monkeypatch.setattr(manage_invites, "SessionLocal", forbidden_factory)
    monkeypatch.setattr(manage_invites, "_is_interactive_terminal", lambda: False)
    assert manage_invites.main(["create"]) == 2
    assert "interactive terminal" in capsys.readouterr().err
    with factory() as db:
        assert db.query(Invitation).count() == 0


def test_migration_on_synthetic_legacy_database_preserves_user_and_session(tmp_path):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "synthetic-legacy.sqlite3"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "f3a4b5c6d7e8")
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO users (username,password_hash,is_active) VALUES (?,?,?)", ("synthetic-legacy", "synthetic-hash", 1))
    user_id = connection.execute("SELECT id FROM users WHERE username='synthetic-legacy'").fetchone()[0]
    connection.execute("INSERT INTO sessions (user_id,token_hash,csrf_token,expires_at) VALUES (?,?,?,?)", (user_id, "synthetic-token-hash", "synthetic-csrf", "2099-01-01 00:00:00"))
    connection.commit()
    connection.close()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT username,password_hash,is_active FROM users WHERE id=?", (user_id,)).fetchone() == ("synthetic-legacy", "synthetic-hash", 1)
        assert connection.execute("SELECT token_hash,csrf_token FROM sessions WHERE user_id=?", (user_id,)).fetchone() == ("synthetic-token-hash", "synthetic-csrf")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] != "f3a4b5c6d7e8"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert "registration_invites" in {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_fresh_migration_and_real_schema_constraints_match_model(tmp_path):
    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "empty-install.sqlite3"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        model_columns = {column.name for column in Invitation.__table__.columns}
        assert model_columns == {column["name"] for column in inspect(engine).get_columns("registration_invites")}
        constraints = inspect(engine).get_unique_constraints("registration_invites")
        assert any(constraint["column_names"] == ["token_hash"] for constraint in constraints)
        assert inspect(engine).get_foreign_keys("registration_invites")[0]["referred_table"] == "users"
        assert {index["name"] for index in inspect(engine).get_indexes("registration_invites")} == {
            "ix_registration_invites_expires_at",
            "ix_registration_invites_claimed_by_user_id",
        }
        assert engine.connect().exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def test_failed_migration_does_not_change_synthetic_legacy_user_or_session(tmp_path):
    import sqlite3

    from alembic import command
    from alembic.config import Config
    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "migration-failure.sqlite3"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "f3a4b5c6d7e8")
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO users (username,password_hash,is_active) VALUES ('failure-user','synthetic-hash',1)")
    user_id = connection.execute("SELECT id FROM users WHERE username='failure-user'").fetchone()[0]
    connection.execute("INSERT INTO sessions (user_id,token_hash,csrf_token,expires_at) VALUES (?,?,?,?)", (user_id, "failure-session", "csrf", "2099-01-01"))
    connection.execute("CREATE TABLE registration_invites (conflicting_schema TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(Exception):
        command.upgrade(config, "head")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT username,password_hash,is_active FROM users WHERE id=?", (user_id,)).fetchone() == ("failure-user", "synthetic-hash", 1)
        assert connection.execute("SELECT token_hash,csrf_token FROM sessions WHERE user_id=?", (user_id,)).fetchone() == ("failure-session", "csrf")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "f3a4b5c6d7e8"
    finally:
        connection.close()
