"""Add content-free task purge receipts for idempotent lifecycle handling."""
from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_purge_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("purged_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "task_id", name="uq_task_purge_receipt_user_task"),
    )
    op.create_index("ix_task_purge_receipts_user_id", "task_purge_receipts", ["user_id"])
    op.create_index("ix_task_purge_receipts_purged_at", "task_purge_receipts", ["purged_at"])


def downgrade() -> None:
    op.drop_index("ix_task_purge_receipts_purged_at", table_name="task_purge_receipts")
    op.drop_index("ix_task_purge_receipts_user_id", table_name="task_purge_receipts")
    op.drop_table("task_purge_receipts")
