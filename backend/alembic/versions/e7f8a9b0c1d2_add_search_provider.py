"""Add per-user encrypted web search credentials.

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
"""
from alembic import op
import sqlalchemy as sa


revision = "e7f8a9b0c1d2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="tavily"),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="not_configured"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_search_providers_user_provider"),
    )
    op.create_index("ix_search_providers_user_id", "search_providers", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_search_providers_user_id", table_name="search_providers")
    op.drop_table("search_providers")
