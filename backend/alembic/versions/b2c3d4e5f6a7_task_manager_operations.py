"""Persist idempotent task-manager operations and undo metadata.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_manager_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_operation_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="started"),
        sa.Column("action_details", sa.JSON(), nullable=False),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "client_operation_id", name="uq_task_manager_operation_user_key"),
    )
    for column in ("user_id", "conversation_id", "user_message_id", "run_id"):
        op.create_index(f"ix_task_manager_operations_{column}", "task_manager_operations", [column])


def downgrade() -> None:
    op.drop_table("task_manager_operations")
