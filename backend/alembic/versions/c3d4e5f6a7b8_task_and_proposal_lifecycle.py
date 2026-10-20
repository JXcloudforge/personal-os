"""Add expiration and proposal deletion lifecycle metadata.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_manager_operations", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_task_manager_operations_expires_at", "task_manager_operations", ["expires_at"])
    op.add_column("agent_proposals", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_agent_proposals_deleted_at", "agent_proposals", ["deleted_at"])
    op.add_column("agent_proposals", sa.Column("purged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_proposals", "purged_at")
    op.drop_index("ix_agent_proposals_deleted_at", table_name="agent_proposals")
    op.drop_column("agent_proposals", "deleted_at")
    op.drop_index("ix_task_manager_operations_expires_at", table_name="task_manager_operations")
    op.drop_column("task_manager_operations", "expires_at")
