"""Add nullable task dates and idempotent daily task snapshots.

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("tasks", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("tasks", sa.Column("expected_duration_days", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_start_date", "tasks", ["start_date"])
    op.create_index("ix_tasks_due_date", "tasks", ["due_date"])
    op.create_table(
        "task_date_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_start_date", sa.Date(), nullable=True),
        sa.Column("new_start_date", sa.Date(), nullable=True),
        sa.Column("old_due_date", sa.Date(), nullable=True),
        sa.Column("new_due_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False, server_default="user_date_change"),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_task_date_history_user_id", "task_date_history", ["user_id"])
    op.create_index("ix_task_date_history_task_id", "task_date_history", ["task_id"])
    op.create_table(
        "daily_task_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("daily_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("status_at_settlement", sa.String(length=20), nullable=False),
        sa.Column("due_date_at_settlement", sa.Date(), nullable=True),
        sa.Column("report_category", sa.String(length=24), nullable=False),
        sa.Column("actual_progress", sa.Text(), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "review_date", "task_id", name="uq_daily_task_snapshot"),
    )
    op.create_index("ix_daily_task_snapshots_user_id", "daily_task_snapshots", ["user_id"])
    op.create_index("ix_daily_task_snapshots_review_id", "daily_task_snapshots", ["review_id"])
    op.create_index("ix_daily_task_snapshots_task_id", "daily_task_snapshots", ["task_id"])
    op.create_index("ix_daily_task_snapshots_review_date", "daily_task_snapshots", ["review_date"])


def downgrade() -> None:
    op.drop_table("daily_task_snapshots")
    op.drop_table("task_date_history")
    op.drop_index("ix_tasks_due_date", table_name="tasks")
    op.drop_index("ix_tasks_start_date", table_name="tasks")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "expected_duration_days")
    op.drop_column("tasks", "due_date")
    op.drop_column("tasks", "start_date")
