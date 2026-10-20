import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_upgrade_backfills_legacy_chat_into_one_owned_conversation(tmp_path):
    database = tmp_path / "legacy-chat.sqlite3"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "a2b3c4d5e6f7")

    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO users (username,password_hash,is_active) VALUES (?,?,?)", ("legacy-chat-user", "test-only-hash", 1))
    user_id = connection.execute("SELECT id FROM users WHERE username=?", ("legacy-chat-user",)).fetchone()[0]
    connection.execute("INSERT INTO chat_messages (user_id,role,content,created_at) VALUES (?,?,?,?)", (user_id, "user", "legacy question", "2026-01-01 10:00:00"))
    connection.execute("INSERT INTO chat_messages (user_id,role,content,created_at) VALUES (?,?,?,?)", (user_id, "assistant", "legacy response", "2026-01-01 10:00:05"))
    connection.commit()
    connection.close()

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "a6c7d8e9f0a1"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        conversations = connection.execute("SELECT id,title,user_id FROM chat_conversations").fetchall()
        assert len(conversations) == 1
        conversation_id, title, owner_id = conversations[0]
        assert title == "legacy question" and owner_id == user_id
        assert connection.execute("SELECT domain,is_pinned,archived_at,deleted_at FROM chat_conversations").fetchone() == ("general", 0, None, None)
        messages = connection.execute("SELECT role,content,status,conversation_id,run_id FROM chat_messages ORDER BY id").fetchall()
        assert messages == [
            ("user", "legacy question", "completed", conversation_id, None),
            ("assistant", "legacy response", "completed", conversation_id, None),
        ]
        assert {row[1] for row in connection.execute("PRAGMA table_info(tasks)")} >= {"estimated_minutes", "is_pinned", "archived_at", "deleted_at", "start_date", "due_date", "expected_duration_days", "completed_at"}
        assert connection.execute("SELECT agent_max_tool_calls,agent_max_output_tokens FROM users WHERE id=?", (user_id,)).fetchone() == (8, 1600)
        assert connection.execute("SELECT COUNT(*) FROM task_conversation_links").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agent_model_usage").fetchone()[0] == 0
        knowledge_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"knowledge_cards", "knowledge_tags", "knowledge_card_tags", "knowledge_source_references", "knowledge_task_links", "knowledge_audit_events"} <= knowledge_tables
    finally:
        connection.close()
