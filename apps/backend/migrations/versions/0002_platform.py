"""Add admin sessions, workspace configuration, scheduling and delivery outbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
TABLES = [
    "admins",
    "admin_sessions",
    "login_buckets",
    "workspaces",
    "channels",
    "participants",
    "reports",
    "deliveries",
    "schedules",
    "occurrences",
    "oauth_states",
    "idempotency",
    "events",
]


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        schema="wordle_bot",
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("admin_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_digest", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["wordle_bot.admins.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_admin_sessions_admin_id"),
        "admin_sessions",
        ["admin_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "login_buckets",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        schema="wordle_bot",
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("bot_id", sa.String(length=64), nullable=False),
        sa.Column("encrypted_token", sa.String(length=4096), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("scopes", sa.String(length=2048), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="wordle_bot",
    )
    op.create_table(
        "channels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("slack_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("roster_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["wordle_bot.workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slack_id", name="uq_workspace_channel"),
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_channels_workspace_id"),
        "channels",
        ["workspace_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "participants",
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["wordle_bot.channels.id"],
        ),
        sa.PrimaryKeyConstraint("channel_id", "user_id"),
        schema="wordle_bot",
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["wordle_bot.channels.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "start", "end", "revision", name="uq_report_revision"),
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_reports_channel_id"),
        "reports",
        ["channel_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("fence", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slack_ts", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("sending_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["wordle_bot.reports.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "part", name="uq_delivery_part"),
        schema="wordle_bot",
    )
    op.create_index(
        "ix_delivery_claim",
        "deliveries",
        ["status", "available_at"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_deliveries_report_id"),
        "deliveries",
        ["report_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("wall_time", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("window", sa.String(length=24), nullable=False),
        sa.Column("rolling_days", sa.Integer(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["wordle_bot.channels.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_schedules_channel_id"),
        "schedules",
        ["channel_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_schedules_next_at"),
        "schedules",
        ["next_at"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "occurrences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["wordle_bot.reports.id"],
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["wordle_bot.schedules.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "scheduled_at", name="uq_occurrence"),
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_occurrences_schedule_id"),
        "occurrences",
        ["schedule_id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_table(
        "oauth_states",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["wordle_bot.admin_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("digest"),
        schema="wordle_bot",
    )
    op.create_table(
        "idempotency",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["wordle_bot.reports.id"],
        ),
        sa.PrimaryKeyConstraint("key"),
        schema="wordle_bot",
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=80), nullable=True),
        sa.Column("target", sa.String(length=80), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="wordle_bot",
    )
    op.create_index(
        "ix_events_filter",
        "events",
        ["workspace_id", "created_at", "id"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_events_category"),
        "events",
        ["category"],
        unique=False,
        schema="wordle_bot",
    )
    op.create_index(
        op.f("ix_wordle_bot_events_created_at"),
        "events",
        ["created_at"],
        unique=False,
        schema="wordle_bot",
    )
    for name in TABLES:
        op.execute(f"ALTER TABLE wordle_bot.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON wordle_bot.{name} FROM PUBLIC")
    op.add_column(
        "participation",
        sa.Column("source_at", sa.DateTime(timezone=True), nullable=True),
        schema="wordle_bot",
    )


def downgrade() -> None:
    op.drop_column("participation", "source_at", schema="wordle_bot")
    for name in reversed(TABLES):
        op.drop_table(name, schema="wordle_bot")
