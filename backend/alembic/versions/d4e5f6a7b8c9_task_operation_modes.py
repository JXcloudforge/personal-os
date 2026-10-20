"""Persist task-manager mode and safe retry identity."""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_manager_operations", sa.Column("mode", sa.String(length=20), nullable=False, server_default="legacy"))
    op.add_column("task_manager_operations", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("task_manager_operations", sa.Column("failure_code", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("task_manager_operations", "failure_code")
    op.drop_column("task_manager_operations", "request_fingerprint")
    op.drop_column("task_manager_operations", "mode")
