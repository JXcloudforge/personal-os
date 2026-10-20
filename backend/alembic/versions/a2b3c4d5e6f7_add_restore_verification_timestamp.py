"""Track last successful user migration-package validation.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""
from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_recovery_validation_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_recovery_validation_at")
