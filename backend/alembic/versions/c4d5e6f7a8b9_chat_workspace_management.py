"""Add domain and lifecycle fields to chat conversations."""

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite's batch mode rebuilds the parent table. With ON DELETE CASCADE enabled
    # that can delete the existing chat_messages while dropping the old table.
    # Native ADD COLUMN preserves the parent identity and all child rows.
    op.add_column("chat_conversations", sa.Column("domain", sa.String(length=30), server_default="general", nullable=False))
    op.add_column("chat_conversations", sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("chat_conversations", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("chat_conversations", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_chat_conversations_domain", "chat_conversations", ["domain"], unique=False)
    op.create_index("ix_chat_conversations_is_pinned", "chat_conversations", ["is_pinned"], unique=False)
    op.create_index("ix_chat_conversations_archived_at", "chat_conversations", ["archived_at"], unique=False)
    op.create_index("ix_chat_conversations_deleted_at", "chat_conversations", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_conversations_deleted_at", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_archived_at", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_is_pinned", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_domain", table_name="chat_conversations")
    op.drop_column("chat_conversations", "deleted_at")
    op.drop_column("chat_conversations", "archived_at")
    op.drop_column("chat_conversations", "is_pinned")
    op.drop_column("chat_conversations", "domain")
