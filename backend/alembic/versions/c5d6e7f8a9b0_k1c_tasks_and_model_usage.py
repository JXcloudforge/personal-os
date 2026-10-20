"""K1-C task workspace links and per-request model usage.

Revision ID: c5d6e7f8a9b0
Revises: c4d5e6f7a8b9
"""
from alembic import op
import sqlalchemy as sa


revision = "c5d6e7f8a9b0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite-native additive changes preserve the parent task table and its
    # existing evidence/status-history rows and foreign keys.
    op.add_column("tasks", sa.Column("estimated_minutes", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_is_pinned", "tasks", ["is_pinned"])
    op.create_index("ix_tasks_archived_at", "tasks", ["archived_at"])
    op.create_index("ix_tasks_deleted_at", "tasks", ["deleted_at"])
    op.add_column("users", sa.Column("agent_max_tool_calls", sa.Integer(), server_default="8", nullable=False))
    op.add_column("users", sa.Column("agent_max_output_tokens", sa.Integer(), server_default="1600", nullable=False))

    op.create_table(
        "task_conversation_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "task_id", "conversation_id", "message_id", name="uq_task_chat_link"),
    )
    for column in ("user_id", "task_id", "conversation_id", "message_id"):
        op.create_index(f"ix_task_conversation_links_{column}", "task_conversation_links", [column])

    op.create_table(
        "agent_model_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_index", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="started", nullable=False),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "request_index", name="uq_agent_model_usage_run_request"),
    )
    for column in ("user_id", "run_id", "conversation_id", "message_id", "requested_at"):
        op.create_index(f"ix_agent_model_usage_{column}", "agent_model_usage", [column])


def downgrade() -> None:
    op.drop_table("agent_model_usage")
    op.drop_table("task_conversation_links")
    op.drop_index("ix_tasks_deleted_at", table_name="tasks")
    op.drop_index("ix_tasks_archived_at", table_name="tasks")
    op.drop_index("ix_tasks_is_pinned", table_name="tasks")
    op.drop_column("tasks", "deleted_at")
    op.drop_column("tasks", "archived_at")
    op.drop_column("tasks", "is_pinned")
    op.drop_column("tasks", "estimated_minutes")
    op.drop_column("users", "agent_max_output_tokens")
    op.drop_column("users", "agent_max_tool_calls")
