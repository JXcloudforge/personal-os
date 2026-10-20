"""Add one-time registration invitations managed by the local CLI."""
from alembic import op
import sqlalchemy as sa


revision = "f4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("issuer_kind", sa.String(length=16), server_default="local_cli", nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_registration_invites_token_hash"),
        sa.CheckConstraint("issuer_kind = 'local_cli'", name="ck_registration_invites_local_issuer"),
        sa.CheckConstraint("NOT (claimed_at IS NOT NULL AND revoked_at IS NOT NULL)", name="ck_registration_invites_claim_or_revoke"),
    )
    op.create_index("ix_registration_invites_expires_at", "registration_invites", ["expires_at"])
    op.create_index("ix_registration_invites_claimed_by_user_id", "registration_invites", ["claimed_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_registration_invites_claimed_by_user_id", table_name="registration_invites")
    op.drop_index("ix_registration_invites_expires_at", table_name="registration_invites")
    op.drop_table("registration_invites")
