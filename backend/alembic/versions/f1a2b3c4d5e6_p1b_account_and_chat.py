"""Add account preferences and persistent private chat.

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("chat_history_enabled", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("users", sa.Column("auto_backup_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("users", sa.Column("backup_interval_days", sa.Integer(), server_default="1", nullable=False))
    op.add_column("users", sa.Column("last_local_backup_at", sa.DateTime(), nullable=True))
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_user_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_column("users", "last_local_backup_at")
    op.drop_column("users", "backup_interval_days")
    op.drop_column("users", "auto_backup_enabled")
    op.drop_column("users", "chat_history_enabled")
    op.drop_column("users", "display_name")
