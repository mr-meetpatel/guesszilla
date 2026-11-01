"""Create private participation, job queue, and worker health tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
SCHEMA = "wordle_bot"


def upgrade() -> None:
    op.create_table(
        "participation",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("channel_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.UniqueConstraint("team_id", "channel_id", "user_id", "day", name="uq_participation_day"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_participation_report", "participation", ["team_id", "channel_id", "day"], schema=SCHEMA
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("delivery_key", sa.String(256), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "available_at"], schema=SCHEMA)
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    for table in ("participation", "jobs", "worker_heartbeats"):
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {SCHEMA}.{table} FROM PUBLIC")
    # Table owners (local application owner / Supabase postgres) retain access.
    # No anonymous/authenticated API grants or public policies are introduced.


def downgrade() -> None:
    for table in ("worker_heartbeats", "jobs", "participation"):
        op.drop_table(table, schema=SCHEMA)
