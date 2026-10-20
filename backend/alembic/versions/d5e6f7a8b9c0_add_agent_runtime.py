"""Add the minimal AI agent runtime tables.

Revision ID: d5e6f7a8b9c0
Revises: c4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "domain", name="uq_user_goals_user_domain"),
    )
    op.create_index("ix_user_goals_user_id", "user_goals", ["user_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workflow", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("authorized_scope", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="started"),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("arguments_summary", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tool_calls_run_id", "agent_tool_calls", ["run_id"])
    op.create_index("ix_agent_tool_calls_user_id", "agent_tool_calls", ["user_id"])

    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("proposal_key", sa.String(length=120), nullable=False),
        sa.Column("proposal_type", sa.String(length=40), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "proposal_key", name="uq_agent_proposals_user_key"),
    )
    op.create_index("ix_agent_proposals_user_id", "agent_proposals", ["user_id"])

    op.create_table(
        "agent_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("proposal_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposal_id"], ["agent_proposals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_audit_events_user_id", "agent_audit_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_audit_events_user_id", table_name="agent_audit_events")
    op.drop_table("agent_audit_events")
    op.drop_index("ix_agent_proposals_user_id", table_name="agent_proposals")
    op.drop_table("agent_proposals")
    op.drop_index("ix_agent_tool_calls_run_id", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_user_id", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_user_goals_user_id", table_name="user_goals")
    op.drop_table("user_goals")
