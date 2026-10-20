"""add encrypted AI provider settings

Revision ID: 8f3d7c0a1b2c
Revises: 693f6298b4bb
"""

from alembic import op
import sqlalchemy as sa


revision = "8f3d7c0a1b2c"
down_revision = "693f6298b4bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="not_configured"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("total_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider"),
    )
    op.create_index("ix_ai_providers_provider", "ai_providers", ["provider"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ai_providers_provider", table_name="ai_providers")
    op.drop_table("ai_providers")
