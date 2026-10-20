import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.auth.security import create_session, hash_password, verify_password
from app.config import settings
from app.database import Base, SessionLocal
from app.main import app
from app.models import AIProvider, AgentRun, ChatConversation, ChatMessage, KnowledgeCard, KnowledgeSourceReference, KnowledgeTaskLink, SessionToken, Task, User
from app.services.provider_service import decrypt_api_key
from app.services.agent_runner import AgentRunError
from app.services import backup_service


def setup_module():
    Base.metadata.create_all(bind=__import__("app.database", fromlist=["engine"]).engine)


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def _new_authed_client():
    username = f"p1b-{Fernet.generate_key().decode()[:10]}"
    password = "Old-Account-Password-2026!"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
        # Create a simulated other-device session before logging in from this test client.
        create_session(db, user)
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client, user_id, username, password


def test_profile_update_keeps_account_id_and_password_change_revokes_other_sessions():
    client, user_id, username, _ = _new_authed_client()
    try:
        profile = client.get("/api/account/profile").json()
        assert profile["id"] == user_id
        assert profile["username"] == username
        assert profile["active_sessions"] == 2
        updated = client.patch("/api/account/profile", headers=_csrf(client), json={"username": username + ".renamed", "display_name": "My desk"})
        assert updated.status_code == 200
        assert updated.json()["id"] == user_id
        changed = client.post("/api/account/password", headers=_csrf(client), json={"current_password": "Old-Account-Password-2026!", "new_password": "New-Account-Password-2026!", "revoke_other_sessions": True})
        assert changed.status_code == 200
        assert changed.json()["other_sessions_revoked"] == 1
        assert client.get("/api/account/profile").status_code == 200
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user and user.username == username + ".renamed" and user.display_name == "My desk"
            assert verify_password("New-Account-Password-2026!", user.password_hash)
            assert db.query(SessionToken).filter_by(user_id=user_id).count() == 1
        assert TestClient(app).post("/api/auth/login", json={"username": username + ".renamed", "password": "Old-Account-Password-2026!"}).status_code == 401
        assert TestClient(app).post("/api/auth/login", json={"username": username + ".renamed", "password": "New-Account-Password-2026!"}).status_code == 200
    finally:
        client.close()


def test_instance_backup_web_routes_are_denied_before_side_effects(monkeypatch, tmp_path):
    client_a, _user_a_id, *_ = _new_authed_client()
    client_b, _user_b_id, *_ = _new_authed_client()
    backup_dir = tmp_path / "must-not-be-created"
    calls = []

    def forbidden_call(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("instance backup service must not be called by a web request")

    monkeypatch.setattr(backup_service, "list_local_backups", forbidden_call)
    monkeypatch.setattr(backup_service, "create_local_backup", forbidden_call)
    monkeypatch.setattr(backup_service, "export_migration_package", forbidden_call)
    monkeypatch.setattr(backup_service, "backup_directory", lambda: backup_dir)
    try:
        # Preserve the established unauthenticated and CSRF failure semantics.
        anonymous = TestClient(app)
        assert anonymous.get("/api/data/backups").status_code == 401
        assert anonymous.post("/api/data/backups", json={}).status_code == 401
        assert anonymous.post("/api/data/migration/export", json={"recovery_password": "Long-Recovery-Passphrase-2026!"}).status_code == 401
        assert client_a.post("/api/data/backups", json={}).json()["detail"] == "CSRF validation failed"

        for client in (client_a, client_b):
            denied = client.get("/api/data/backups")
            assert denied.status_code == 403
            assert denied.json()["detail"]["code"] == "INSTANCE_BACKUP_WEB_DISABLED"
            assert client.post("/api/data/backups", headers=_csrf(client), json={}).status_code == 403
            exported = client.post("/api/data/migration/export", headers=_csrf(client), json={"recovery_password": "Long-Recovery-Passphrase-2026!"})
            assert exported.status_code == 403
            assert exported.json()["detail"]["code"] == "INSTANCE_BACKUP_WEB_DISABLED"
        assert calls == []
        assert not backup_dir.exists()
    finally:
        client_a.close()
        client_b.close()


def test_web_user_cannot_change_instance_backup_scheduler_controls_but_can_change_chat_privacy():
    client, user_id, *_ = _new_authed_client()
    try:
        response = client.patch("/api/account/data-settings", headers=_csrf(client), json={"auto_backup_enabled": True, "backup_interval_days": 1})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "INSTANCE_BACKUP_WEB_DISABLED"
        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.auto_backup_enabled is False
        allowed = client.patch("/api/account/data-settings", headers=_csrf(client), json={"chat_history_enabled": False})
        assert allowed.status_code == 200
        assert allowed.json()["chat_history_enabled"] is False
    finally:
        client.close()


def test_chat_is_persisted_per_user_and_can_be_cleared(monkeypatch):
    client, user_id, _, _ = _new_authed_client()

    async def fake_run_chat(db, uid, message, history, **kwargs):
        assert uid == user_id
        return {"content": "Mocked response", "tools": [], "run_id": None}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        result = client.post("/api/agent/chat", headers=_csrf(client), json={"message": "test persist", "history": [], "consent_granted": False})
        assert result.status_code == 200
        history = client.get("/api/agent/chat/history").json()
        assert [row["content"] for row in history["messages"]] == ["test persist", "Mocked response"]
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(user_id=user_id).count() == 2
        disabled = client.patch("/api/account/data-settings", headers=_csrf(client), json={"chat_history_enabled": False})
        assert disabled.status_code == 200 and disabled.json()["chat_history_enabled"] is False
        assert client.get("/api/agent/chat/history").json() == {"enabled": False, "messages": []}
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(user_id=user_id).count() == 0
            assert db.query(ChatConversation).filter_by(user_id=user_id).count() == 0
    finally:
        client.close()


def test_conversation_scoped_chat_three_turns_reload_isolation_and_run_linkage(monkeypatch):
    client, user_id, username, password = _new_authed_client()
    observed_histories = []

    async def fake_run_chat(db, uid, message, history, **kwargs):
        observed_histories.append(list(history))
        run = AgentRun(user_id=uid, workflow="agent_chat", mode="mock", provider="mock", model="mock", authorized_scope="user_message", status="completed")
        db.add(run)
        db.flush()
        return {"content": f"reply-{len(observed_histories)}", "tools": [], "run_id": run.id}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    headers = _csrf(client)
    try:
        created = client.post("/api/agent/chat/conversations", headers=headers, json={})
        assert created.status_code == 200
        conversation_id = created.json()["id"]
        for content in ("turn one", "turn two", "turn three"):
            response = client.post("/api/agent/chat", headers=headers, json={"message": content, "conversation_id": conversation_id, "history": []})
            assert response.status_code == 200
            assert response.json()["conversation_id"] == conversation_id
            assert response.json()["run_id"] is not None
        assert [len(history) for history in observed_histories] == [0, 2, 4]
        assert observed_histories[2][-2:] == [{"role": "user", "content": "turn two"}, {"role": "assistant", "content": "reply-2"}]
        reopened = client.get(f"/api/agent/chat/conversations/{conversation_id}")
        assert reopened.status_code == 200
        messages = reopened.json()["messages"]
        assert [item["content"] for item in messages] == ["turn one", "reply-1", "turn two", "reply-2", "turn three", "reply-3"]
        assert all(item["status"] == "completed" and item["run_id"] is not None for item in messages)
        assert messages[0]["run_id"] == messages[1]["run_id"]
        assert messages[2]["run_id"] == messages[3]["run_id"]

        second = client.post("/api/agent/chat/conversations", headers=headers, json={}).json()
        response = client.post("/api/agent/chat", headers=headers, json={"message": "separate topic", "conversation_id": second["id"]})
        assert response.status_code == 200
        assert observed_histories[-1] == []
        assert client.get(f"/api/agent/chat/conversations/{second['id']}").json()["messages"][0]["content"] == "separate topic"

        other, _, _, _ = _new_authed_client()
        try:
            assert other.get(f"/api/agent/chat/conversations/{conversation_id}").status_code == 404
            assert other.post("/api/agent/chat", headers=_csrf(other), json={"message": "cross user", "conversation_id": conversation_id}).status_code == 404
        finally:
            other.close()

        client.close()
        refreshed = TestClient(app)
        assert refreshed.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
        assert len(refreshed.get(f"/api/agent/chat/conversations/{conversation_id}").json()["messages"]) == 6
        refreshed.close()
    finally:
        client.close()


def test_failed_chat_turn_is_persisted_as_failed_without_fabricated_assistant(monkeypatch):
    client, user_id, _, _ = _new_authed_client()
    conversation_id = client.post("/api/agent/chat/conversations", headers=_csrf(client), json={}).json()["id"]

    async def failed_run(db, uid, message, history, **kwargs):
        run = AgentRun(user_id=uid, workflow="agent_chat", mode="mock", provider="mock", model="mock", authorized_scope="user_message", status="failed", error_code="MODEL_TIMEOUT")
        db.add(run)
        db.flush()
        error = AgentRunError("MODEL_TIMEOUT", "Mock timeout")
        error.run_id = run.id
        raise error

    monkeypatch.setattr("app.api.routes.run_chat", failed_run)
    try:
        response = client.post("/api/agent/chat", headers=_csrf(client), json={"message": "a question that times out", "conversation_id": conversation_id})
        assert response.status_code == 504
        messages = client.get(f"/api/agent/chat/conversations/{conversation_id}").json()["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user" and messages[0]["status"] == "failed"
        assert messages[0]["run_id"] is not None
        assert client.get(f"/api/agent/chat/conversations/{conversation_id}").json()["messages"][0]["content"] == "a question that times out"
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(user_id=user_id, role="assistant").count() == 0
    finally:
        client.close()


def test_chat_privacy_disabled_uses_only_ephemeral_client_history(monkeypatch):
    client, user_id, _, _ = _new_authed_client()

    async def fake_run_chat(db, uid, message, history, **kwargs):
        assert history == [{"role": "user", "content": "only in this tab"}]
        return {"content": "ephemeral reply", "tools": [], "run_id": None}

    monkeypatch.setattr("app.api.routes.run_chat", fake_run_chat)
    try:
        disabled = client.patch("/api/account/data-settings", headers=_csrf(client), json={"chat_history_enabled": False})
        assert disabled.status_code == 200
        result = client.post("/api/agent/chat", headers=_csrf(client), json={"message": "continue ephemerally", "history": [{"role": "user", "content": "only in this tab"}]})
        assert result.status_code == 200
        assert result.json()["conversation_id"] is None
        assert client.get("/api/agent/chat/conversations").json() == {"enabled": False, "conversations": []}
        with SessionLocal() as db:
            assert db.query(ChatMessage).filter_by(user_id=user_id).count() == 0
            assert db.query(ChatConversation).filter_by(user_id=user_id).count() == 0
    finally:
        client.close()


def _isolated_bundle_db(tmp_path, monkeypatch):
    db_path = tmp_path / "source.sqlite3"
    isolated = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(isolated)
    with isolated.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version(version_num) VALUES (:rev)"), {"rev": backup_service.CURRENT_REVISION})
    monkeypatch.setattr(backup_service, "engine", isolated)
    monkeypatch.setattr(backup_service, "SessionLocal", __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(bind=isolated, expire_on_commit=False))
    monkeypatch.setattr(backup_service, "backup_directory", lambda: tmp_path / "backup-store")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credential_master_key", key)
    with Session(isolated) as db:
        user = User(username="bundle-user", password_hash=hash_password("Local-test-password"))
        db.add(user)
        db.flush()
        provider = AIProvider(user_id=user.id, provider="deepseek", base_url="https://api.deepseek.com", model="deepseek-flash", encrypted_api_key=Fernet(key.encode()).encrypt(b"synthetic-test-credential").decode(), enabled=True, status="configured")
        db.add(provider)
        db.add(Task(user_id=user.id, title="Preserve task", module="system"))
        db.add(ChatMessage(user_id=user.id, role="assistant", content="Persisted conversation"))
        db.commit()
        return isolated, key


def test_encrypted_migration_validates_credentials_and_restores_to_new_path(tmp_path, monkeypatch):
    isolated, key = _isolated_bundle_db(tmp_path, monkeypatch)
    try:
        passphrase = "Long-Recovery-Passphrase-2026!"
        filename, package, metadata = backup_service.export_migration_package(passphrase)
        assert filename.endswith(".posmig")
        assert b"synthetic-test-credential" not in package
        assert key.encode() not in package
        assert metadata["status"] == "encrypted_package"
        validation = backup_service.validate_migration_package(package, passphrase)
        assert validation["valid"] is True
        assert validation["provider_credentials_verified"] is True
        assert validation["table_counts"]["tasks"] == 1
        assert validation["table_counts"]["chat_messages"] == 1
        with pytest.raises(ValueError, match="incorrect"):
            backup_service.validate_migration_package(package, "Wrong-Recovery-Passphrase-2026!")
        target = tmp_path / "restore" / "personal_os.sqlite3"
        restored = backup_service.restore_migration_package(package, passphrase, target)
        assert restored["sessions_revoked"] is True
        assert restored["requires_manual_activation"] is True
        with Session(create_engine(f"sqlite:///{target.as_posix()}")) as db:
            assert db.scalar(select(User.username)) == "bundle-user"
            assert db.scalar(select(Task.title)) == "Preserve task"
            assert db.scalar(select(ChatMessage.content)) == "Persisted conversation"
            restored_provider = db.scalar(select(AIProvider))
            assert decrypt_api_key(restored_provider) == "synthetic-test-credential"
            assert db.query(SessionToken).count() == 0
        with pytest.raises(FileExistsError):
            backup_service.restore_migration_package(package, passphrase, target)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        isolated.dispose()


def test_encrypted_migration_package_preserves_knowledge_and_task_source_links(tmp_path, monkeypatch):
    isolated, key = _isolated_bundle_db(tmp_path, monkeypatch)
    try:
        with Session(isolated) as db:
            user = db.scalar(select(User).where(User.username == "bundle-user"))
            task = db.scalar(select(Task).where(Task.title == "Preserve task"))
            card = KnowledgeCard(user_id=user.id, title="Preserve knowledge", content_markdown="# Knowledge", domain="stem")
            db.add(card); db.flush()
            db.add(KnowledgeTaskLink(user_id=user.id, card_id=card.id, task_id=task.id))
            db.add(KnowledgeSourceReference(card_id=card.id, user_id=user.id, evidence_kind="SOURCE_FETCHED", source_url="https://example.org/source", source_title="Source", fetch_status="fetched_body"))
            db.commit()
            card_id, task_id = card.id, task.id
        passphrase = "Long-Recovery-Passphrase-2026!"
        _, package, _ = backup_service.export_migration_package(passphrase)
        target = tmp_path / "knowledge-restore.sqlite3"
        validation = backup_service.validate_migration_package(package, passphrase)
        assert validation["table_counts"]["knowledge_cards"] == 1
        assert validation["table_counts"]["knowledge_source_references"] == 1
        assert validation["table_counts"]["knowledge_task_links"] == 1
        backup_service.restore_migration_package(package, passphrase, target)
        restored_engine = create_engine(f"sqlite:///{target.as_posix()}")
        try:
            with Session(restored_engine) as db:
                restored_card = db.get(KnowledgeCard, card_id)
                assert restored_card and restored_card.content_markdown == "# Knowledge"
                link = db.scalar(select(KnowledgeTaskLink).where(KnowledgeTaskLink.card_id == card_id, KnowledgeTaskLink.task_id == task_id))
                source = db.scalar(select(KnowledgeSourceReference).where(KnowledgeSourceReference.card_id == card_id))
                assert link is not None
                assert source and source.fetch_status == "fetched_body"
        finally:
            restored_engine.dispose()
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        isolated.dispose()


def test_production_initial_account_creation_is_local_cli_only(monkeypatch, tmp_path):
    from app.config import settings

    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "production-setup.sqlite3"
    migration = Config(str(backend / "alembic.ini"))
    migration.set_main_option("script_location", str(backend / "alembic"))
    migration.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(migration, "head")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database.as_posix()}")
    monkeypatch.setattr(settings, "runtime_environment", "production")
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "secret_key", "test-only-long-production-secret-value")
    with TestClient(app) as client:
        result = client.post("/api/auth/setup", json={"username": "web-admin", "password": "Long-enough-password"})
        assert result.status_code == 403


def test_migration_rejects_zip_path_traversal_and_local_backup_is_verified(tmp_path, monkeypatch):
    isolated, _ = _isolated_bundle_db(tmp_path, monkeypatch)
    passphrase = "Long-Recovery-Passphrase-2026!"
    try:
        salt = b"0123456789abcdef"
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../manifest.json", "{}")
            archive.writestr("database.sqlite3", b"db")
            archive.writestr("credential_master_key.txt", "not-a-key")
        package = backup_service.FORMAT_MAGIC + salt + backup_service._derive_cipher(passphrase, salt).encrypt(unsafe.getvalue())
        with pytest.raises(ValueError, match="file list"):
            backup_service.validate_migration_package(package, passphrase)
        backup = backup_service.create_local_backup()
        assert backup["status"] == "verified"
        assert backup["quick_check"] == "ok"
        assert backup["foreign_key_violations"] == 0
        listed = backup_service.list_local_backups()
        assert any(item["id"] == backup["id"] for item in listed)
    finally:
        isolated.dispose()


def test_local_backup_accepts_pre_k2b_database_revision_before_incremental_migration(tmp_path, monkeypatch):
    isolated, _ = _isolated_bundle_db(tmp_path, monkeypatch)
    try:
        with isolated.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='e8f9a0b1c2d3'"))
        backup = backup_service.create_local_backup()
        assert backup["status"] == "verified"
        assert backup["revision"] == "e8f9a0b1c2d3"
        assert backup["quick_check"] == "ok"
        assert backup["foreign_key_violations"] == 0
        backup_path = str((tmp_path / "backup-store" / f"{backup['id']}.sqlite3")).replace(chr(92), "/")
        with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "e8f9a0b1c2d3"
    finally:
        isolated.dispose()


def test_environment_provider_credential_becomes_portable_encrypted_database_credential(tmp_path, monkeypatch):
    isolated, key = _isolated_bundle_db(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "deepseek_api_key", "synthetic-environment-key")
    with Session(isolated) as db:
        provider = db.scalar(select(AIProvider).where(AIProvider.provider == "deepseek"))
        provider.encrypted_api_key = None
        db.commit()
    try:
        passphrase = "Long-Recovery-Passphrase-2026!"
        _, package, _ = backup_service.export_migration_package(passphrase)
        target = tmp_path / "env-credential-restore.sqlite3"
        backup_service.restore_migration_package(package, passphrase, target)
        restored_engine = create_engine(f"sqlite:///{target.as_posix()}")
        try:
            with Session(restored_engine) as db:
                provider = db.scalar(select(AIProvider).where(AIProvider.provider == "deepseek"))
                assert provider.encrypted_api_key is not None
                assert decrypt_api_key(provider) == "synthetic-environment-key"
        finally:
            restored_engine.dispose()
    finally:
        isolated.dispose()
