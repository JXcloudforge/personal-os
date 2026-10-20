"""Add explicit user ownership to private records.

Revision ID: c4e5f6a7b8c9
Revises: 8f3d7c0a1b2c
"""

from alembic import op
import sqlalchemy as sa


revision = "c4e5f6a7b8c9"
down_revision = "8f3d7c0a1b2c"
branch_labels = None
depends_on = None

PARENT_TABLES = [
    "tasks",
    "study_records",
    "workouts",
    "health_metrics",
    "daily_reviews",
    "ai_requests",
    "ai_providers",
]


def _owner_id(connection):
    user_ids = [row[0] for row in connection.execute(sa.text("SELECT id FROM users ORDER BY id"))]
    rows_by_table = {
        table: connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in PARENT_TABLES
    }
    if any(rows_by_table.values()) and len(user_ids) != 1:
        raise RuntimeError(
            "Cannot safely backfill user ownership: existing private records require exactly one existing user"
        )
    return user_ids[0] if len(user_ids) == 1 else None


def _rebuild_sqlite_table(connection, table: str, create_sql: str, copy_sql: str, owner_id: int | None) -> None:
    connection.execute(sa.text(create_sql))
    if owner_id is not None:
        connection.execute(sa.text(copy_sql), {"owner_id": owner_id})
    connection.execute(sa.text(f"DROP TABLE {table}"))
    connection.execute(sa.text(f"ALTER TABLE {table}_new RENAME TO {table}"))


def upgrade() -> None:
    connection = op.get_bind()
    owner_id = _owner_id(connection)

    if connection.dialect.name == "sqlite":
        for table in ["tasks", "study_records", "workouts", "health_metrics", "ai_requests"]:
            op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
            if owner_id is not None:
                connection.execute(sa.text(f"UPDATE {table} SET user_id = :owner_id WHERE user_id IS NULL"), {"owner_id": owner_id})
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column("user_id", nullable=False)
                batch.create_foreign_key(f"fk_{table}_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")
                batch.create_index(f"ix_{table}_user_id", ["user_id"])

        _rebuild_sqlite_table(
            connection,
            "daily_reviews",
            """CREATE TABLE daily_reviews_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                review_date DATE NOT NULL,
                planned_summary TEXT,
                actual_summary TEXT,
                blockers TEXT,
                next_actions TEXT,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                CONSTRAINT uq_daily_reviews_user_date UNIQUE (user_id, review_date),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """INSERT INTO daily_reviews_new
                (id, user_id, review_date, planned_summary, actual_summary, blockers, next_actions, created_at, updated_at)
                SELECT id, :owner_id, review_date, planned_summary, actual_summary, blockers, next_actions, created_at, updated_at
                FROM daily_reviews""",
            owner_id,
        )
        connection.execute(sa.text("CREATE INDEX ix_daily_reviews_user_id ON daily_reviews (user_id)"))

        _rebuild_sqlite_table(
            connection,
            "ai_providers",
            """CREATE TABLE ai_providers_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                provider VARCHAR(40) NOT NULL,
                base_url VARCHAR(300) NOT NULL,
                model VARCHAR(120) NOT NULL,
                encrypted_api_key TEXT,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                status VARCHAR(30) NOT NULL DEFAULT 'not_configured',
                last_error TEXT,
                total_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                total_completion_tokens INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                CONSTRAINT uq_ai_providers_user_provider UNIQUE (user_id, provider),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""",
            """INSERT INTO ai_providers_new
                (id, user_id, provider, base_url, model, encrypted_api_key, enabled, status, last_error,
                 total_prompt_tokens, total_completion_tokens, created_at, updated_at)
                SELECT id, :owner_id, provider, base_url, model, encrypted_api_key, enabled, status, last_error,
                       total_prompt_tokens, total_completion_tokens, created_at, updated_at
                FROM ai_providers""",
            owner_id,
        )
        connection.execute(sa.text("CREATE INDEX ix_ai_providers_user_id ON ai_providers (user_id)"))
        connection.execute(sa.text("CREATE INDEX ix_ai_providers_provider ON ai_providers (provider)"))
        return

    # PostgreSQL and other future dialects can use native ALTER TABLE operations.
    for table in PARENT_TABLES:
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        if owner_id is not None:
            connection.execute(sa.text(f"UPDATE {table} SET user_id = :owner_id WHERE user_id IS NULL"), {"owner_id": owner_id})
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_user_id", table, "users", ["user_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    op.create_unique_constraint("uq_daily_reviews_user_date", "daily_reviews", ["user_id", "review_date"])
    op.create_unique_constraint("uq_ai_providers_user_provider", "ai_providers", ["user_id", "provider"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # Downgrade is intentionally conservative: ownership data must not be
        # silently discarded by an automatic rollback.
        raise RuntimeError("Ownership migration downgrade requires an explicit data-preserving procedure")
    for table in reversed(PARENT_TABLES):
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_column(table, "user_id")
