"""Add per-user appearance preferences.

Revision ID: f2a3b4c5d6e7
Revises: c5d6e7f8a9b0
"""
from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("theme", sa.String(length=24), server_default="classic", nullable=False))
    op.add_column("users", sa.Column("theme_background_filename", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("theme_background_darkness", sa.Integer(), server_default="48", nullable=False))
    op.add_column("users", sa.Column("theme_background_blur", sa.Integer(), server_default="3", nullable=False))
    op.add_column("users", sa.Column("theme_background_opacity", sa.Integer(), server_default="48", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "theme_background_opacity")
    op.drop_column("users", "theme_background_blur")
    op.drop_column("users", "theme_background_darkness")
    op.drop_column("users", "theme_background_filename")
    op.drop_column("users", "theme")
