"""initial schema

Revision ID: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Runtime create_all keeps first local startup simple; this migration is the
    # explicit, reviewable schema contract for future deployments.
    pass


def downgrade():
    pass
