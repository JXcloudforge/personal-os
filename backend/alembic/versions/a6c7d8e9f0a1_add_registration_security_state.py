"""Persist registration throttles and one-time CSRF challenges."""
from alembic import op
import sqlalchemy as sa


revision = "a6c7d8e9f0a1"
down_revision = "f4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_security_state",
        sa.Column("state_hash", sa.String(length=64), primary_key=True),
        sa.Column("state_kind", sa.String(length=24), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_auth_security_state_state_kind", "auth_security_state", ["state_kind"])
    op.create_index("ix_auth_security_state_expires_at", "auth_security_state", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_security_state_expires_at", table_name="auth_security_state")
    op.drop_index("ix_auth_security_state_state_kind", table_name="auth_security_state")
    op.drop_table("auth_security_state")
