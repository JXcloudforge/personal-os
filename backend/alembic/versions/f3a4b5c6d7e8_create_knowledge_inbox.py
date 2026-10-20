"""Create the unified, user-scoped Knowledge inbox."""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("knowledge_type", sa.String(24), nullable=False, server_default="Concept"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    for column in ("user_id", "domain", "review_status", "is_pinned", "archived_at", "deleted_at", "purged_at"):
        op.create_index(f"ix_knowledge_cards_{column}", "knowledge_cards", [column])

    op.create_table(
        "knowledge_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_knowledge_tags_user_name"),
    )
    op.create_index("ix_knowledge_tags_user_id", "knowledge_tags", ["user_id"])

    op.create_table(
        "knowledge_card_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("knowledge_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("knowledge_tags.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("card_id", "tag_id", name="uq_knowledge_card_tag"),
    )
    op.create_index("ix_knowledge_card_tags_card_id", "knowledge_card_tags", ["card_id"])
    op.create_index("ix_knowledge_card_tags_tag_id", "knowledge_card_tags", ["tag_id"])

    op.create_table(
        "knowledge_source_references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("knowledge_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("source_title", sa.String(300), nullable=True),
        sa.Column("fetch_status", sa.String(32), nullable=False, server_default="not_applicable"),
        sa.Column("retrieved_at", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    for column in ("card_id", "user_id", "conversation_id", "message_id", "agent_run_id"):
        op.create_index(f"ix_knowledge_source_references_{column}", "knowledge_source_references", [column])

    op.create_table(
        "knowledge_task_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("knowledge_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "card_id", "task_id", name="uq_knowledge_task_link"),
    )
    for column in ("user_id", "card_id", "task_id"):
        op.create_index(f"ix_knowledge_task_links_{column}", "knowledge_task_links", [column])

    op.create_table(
        "knowledge_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_audit_events_user_id", "knowledge_audit_events", ["user_id"])
    op.create_index("ix_knowledge_audit_events_card_id", "knowledge_audit_events", ["card_id"])


def downgrade() -> None:
    op.drop_table("knowledge_audit_events")
    op.drop_table("knowledge_task_links")
    op.drop_table("knowledge_source_references")
    op.drop_table("knowledge_card_tags")
    op.drop_table("knowledge_tags")
    op.drop_table("knowledge_cards")
