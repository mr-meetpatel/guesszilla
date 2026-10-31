"""Private-schema platform state. All public serializers use explicit field lists."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wordle_bot.repositories.models import Base


def identifier() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    admin_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.admins.id"), index=True)
    refresh_digest: Mapped[str] = mapped_column(String(64))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginBucket(Base):
    __tablename__ = "login_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    bot_id: Mapped[str] = mapped_column(String(64), default="")
    encrypted_token: Mapped[str] = mapped_column(String(4096))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    scopes: Mapped[str] = mapped_column(String(2048), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("workspace_id", "slack_id", name="uq_workspace_channel"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.workspaces.id"), index=True)
    slack_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    roster_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Participant(Base):
    __tablename__ = "participants"
    channel_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.channels.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("channel_id", "start", "end", "revision", name="uq_report_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    channel_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.channels.id"), index=True)
    start: Mapped[date]
    end: Mapped[date]
    revision: Mapped[int] = mapped_column(default=0)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("report_id", "part", name="uq_delivery_part"),
        Index("ix_delivery_claim", "status", "available_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    report_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.reports.id"), index=True)
    part: Mapped[int]
    text: Mapped[str]
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    fence: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(64))
    sending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    channel_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.channels.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    frequency: Mapped[str] = mapped_column(String(16))
    weekdays: Mapped[list[int]] = mapped_column(JSONB)
    wall_time: Mapped[str] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(80))
    window: Mapped[str] = mapped_column(String(24))
    rolling_days: Mapped[int] = mapped_column(default=7)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(default=1)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Occurrence(Base):
    __tablename__ = "occurrences"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_at", name="uq_occurrence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.schedules.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    report_id: Mapped[str | None] = mapped_column(ForeignKey("wordle_bot.reports.id"))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.admin_sessions.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Idempotency(Base):
    __tablename__ = "idempotency"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    report_id: Mapped[str] = mapped_column(ForeignKey("wordle_bot.reports.id"))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_filter", "workspace_id", "created_at", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    action: Mapped[str] = mapped_column(String(120))
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str | None] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(80))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
