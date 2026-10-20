"""Add conversation identity and run-linked chat messages.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False, server_default="新会话"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_chat_conversations_user_id", "chat_conversations", ["user_id"])
    op.create_index("ix_chat_conversations_updated_at", "chat_conversations", ["updated_at"])
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("conversation_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"))
        batch.create_foreign_key("fk_chat_messages_conversation_id", "chat_conversations", ["conversation_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_chat_messages_run_id", "agent_runs", ["run_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])
    op.create_index("ix_chat_messages_run_id", "chat_messages", ["run_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT user_id, MIN(id) AS first_id, MIN(created_at) AS first_at, MAX(created_at) AS last_at "
        "FROM chat_messages GROUP BY user_id"
    )).mappings().all()
    for row in rows:
        first_user_message = bind.execute(
            sa.text("SELECT content FROM chat_messages WHERE user_id=:uid AND role='user' ORDER BY id LIMIT 1"),
            {"uid": row["user_id"]},
        ).scalar()
        title = (first_user_message or "历史对话").strip().replace("\n", " ")[:120] or "历史对话"
        conversation_id = str(uuid4())
        bind.execute(sa.text(
            "INSERT INTO chat_conversations (id,user_id,title,created_at,updated_at) "
            "VALUES (:id,:uid,:title,:created,:updated)"
        ), {"id": conversation_id, "uid": row["user_id"], "title": title, "created": row["first_at"], "updated": row["last_at"]})
        bind.execute(sa.text("UPDATE chat_messages SET conversation_id=:cid WHERE user_id=:uid"), {"cid": conversation_id, "uid": row["user_id"]})


def downgrade() -> None:
    op.drop_index("ix_chat_messages_run_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_constraint("fk_chat_messages_run_id", type_="foreignkey")
        batch.drop_constraint("fk_chat_messages_conversation_id", type_="foreignkey")
        batch.drop_column("status")
        batch.drop_column("run_id")
        batch.drop_column("conversation_id")
    op.drop_index("ix_chat_conversations_updated_at", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_user_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
