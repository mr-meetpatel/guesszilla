"""Immutable report snapshots and an outbox that never blindly retries an uncertain send."""

import json
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from cryptography.fernet import Fernet, MultiFernet
from flask import abort
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy import Engine, or_, select, text, update
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.platform.auth import audit, digest
from wordle_bot.platform.models import (
    Channel,
    Delivery,
    Event,
    Idempotency,
    Participant,
    Report,
    Workspace,
    identifier,
    utcnow,
)
from wordle_bot.platform.rules import ReportRules, render_snapshot
from wordle_bot.repositories.models import Participation


def crypto(settings: Settings) -> MultiFernet:
    return MultiFernet(
        [
            Fernet(key.encode())
            for key in settings.credential_encryption_keys.get_secret_value().split(",")
        ]
    )


def report_dict(report: Report) -> dict[str, Any]:
    return {
        "id": report.id,
        "channel_id": report.channel_id,
        "start": report.start.isoformat(),
        "end": report.end.isoformat(),
        "revision": report.revision,
        "status": report.status,
        "snapshot": report.snapshot,
        "created_at": report.created_at.isoformat(),
    }


class ReportService:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings, self.engine = settings, engine

    def snapshot(self, db: Session, channel: Channel, start: date, end: date) -> dict[str, Any]:
        if end < start or (end - start).days > 365:
            abort(422, description="Choose an inclusive range of at most 366 days")
        rules = ReportRules(**channel.rules)
        eligible = [
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() in rules.weekdays
        ]
        roster = list(
            db.scalars(
                select(Participant).where(
                    Participant.channel_id == channel.id,
                    Participant.active.is_(True),
                    Participant.included.is_(True),
                )
            )
        )
        rows = db.execute(
            select(Participation.user_id, Participation.day).where(
                Participation.team_id == channel.workspace_id,
                Participation.channel_id == channel.slack_id,
                Participation.day.between(start, end),
            )
        )
        by_user: dict[str, list[str]] = {}
        for user, day in rows:
            if day.weekday() in rules.weekdays:
                by_user.setdefault(user, []).append(day.isoformat())
        players: list[dict[str, Any]] = sorted(
            [
                {
                    "user_id": p.user_id,
                    "name": p.name,
                    "days": len(by_user.get(p.user_id, [])),
                    "dates": sorted(by_user.get(p.user_id, [])),
                }
                for p in roster
            ],
            key=lambda p: (str(p["name"]).casefold(), str(p["user_id"])),
        )
        participants = sum(int(p["days"]) > 0 for p in players)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": rules.timezone,
            "threshold": rules.threshold,
            "eligible_days": len(eligible),
            "eligible_dates": [d.isoformat() for d in eligible],
            "players": players,
            "roster_size": len(roster),
            "participating": participants,
            "ratio": participants / len(roster) if roster else None,
            "royalties": sum(int(p["days"]) >= rules.threshold for p in players),
            "rules_version": channel.version,
            "announce": rules.announce,
            "title": rules.title,
            "cutoff": utcnow().isoformat(),
            "destination": channel.slack_id,
            "roster_synced_at": channel.roster_synced_at.isoformat()
            if channel.roster_synced_at
            else None,
        }

    def request_report(
        self,
        channel_id: str,
        start: date,
        end: date,
        key: str,
        actor: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with Session(self.engine) as db, db.begin():
            return report_dict(
                self.create(
                    db, channel_id, start, end, key, actor, expected_revision=expected_revision
                )
            )

    def create(
        self,
        db: Session,
        channel_id: str,
        start: date,
        end: date,
        key: str,
        actor: str,
        *,
        expected_revision: int | None = None,
    ) -> Report:
        if not 1 <= len(key) <= 256:
            abort(422, description="Idempotency-Key is required (max 256 characters)")
        hashed_key = digest(key)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(hashed_key[:15], 16)})
        intent = digest(json.dumps([channel_id, str(start), str(end), expected_revision]))
        previous = db.get(Idempotency, hashed_key)
        if previous:
            if previous.request_hash != intent:
                abort(409, description="Idempotency key was used for a different request")
            result = db.get(Report, previous.report_id)
            assert result is not None
            return result
        channel = db.scalar(select(Channel).where(Channel.id == channel_id).with_for_update())
        if not channel or not channel.active:
            abort(404, description="Active channel not found")
        workspace = db.get(Workspace, channel.workspace_id)
        if not workspace or not workspace.active:
            abort(409, description="Workspace is paused")
        latest = db.scalar(
            select(Report)
            .where(Report.channel_id == channel_id, Report.start == start, Report.end == end)
            .order_by(Report.revision.desc())
            .limit(1)
        )
        if expected_revision is not None:
            if not latest or latest.revision != expected_revision:
                abort(409, description="Report revision changed; reload before resending")
            if latest.status not in {"delivered", "failed", "cancelled"}:
                abort(409, description="Resolve pending or uncertain delivery before resending")
        if latest and expected_revision is None:
            result = latest
        else:
            snapshot = self.snapshot(db, channel, start, end)
            if snapshot["eligible_days"] < snapshot["threshold"]:
                abort(422, description="Royalty threshold exceeds eligible days in this window")
            result = Report(
                id=identifier(),
                channel_id=channel_id,
                start=start,
                end=end,
                revision=latest.revision + 1 if latest else 0,
                snapshot=snapshot,
                status="pending",
            )
            db.add(result)
            db.flush()
            for part, content in enumerate(render_snapshot(snapshot)):
                db.add(Delivery(report_id=result.id, part=part, text=content))
            audit(
                db,
                "report.resend" if latest else "report.created",
                actor=actor,
                target=result.id,
                workspace=channel.workspace_id,
            )
        db.add(Idempotency(key=hashed_key, request_hash=intent, report_id=result.id))
        db.flush()
        return result


class OutboxDispatcher:
    def __init__(
        self, settings: Settings, engine: Engine, client_factory: Callable[[str], Any] | None = None
    ) -> None:
        self.settings, self.engine = settings, engine
        self.client_factory = client_factory or (
            lambda token: WebClient(token=token, timeout=10, retry_handlers=[])
        )

    def _finish(
        self,
        delivery_id: str,
        fence: str,
        status: str,
        *,
        slack_ts: str | None = None,
        error: str | None = None,
        delay: float = 0,
    ) -> None:
        with Session(self.engine) as db, db.begin():
            delivery = db.scalar(
                select(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.fence == fence,
                    Delivery.status.in_(["sending", "uncertain", "pending"]),
                )
                .with_for_update()
            )
            if not delivery:
                return
            delivery.status, delivery.slack_ts, delivery.last_error = status, slack_ts, error
            delivery.lease_until = None
            delivery.available_at = utcnow() + timedelta(seconds=delay)
            db.flush()
            states = list(
                db.scalars(select(Delivery.status).where(Delivery.report_id == delivery.report_id))
            )
            report = db.get(Report, delivery.report_id)
            assert report is not None
            report.status = (
                "delivered"
                if all(state == "sent" for state in states)
                else "uncertain"
                if "uncertain" in states
                else "failed"
                if "failed" in states
                else "pending"
            )
            if self.settings.persist_logs:
                db.add(
                    Event(
                        category="delivery",
                        action="delivery." + status,
                        target=delivery.id,
                        level="ERROR" if status in {"failed", "uncertain"} else "INFO",
                        details={
                            "report_id": report.id,
                            "part": delivery.part,
                            "attempt": delivery.attempts,
                            "error_code": error,
                        },
                    )
                )

    def tick(self) -> bool:
        now = utcnow()
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(Delivery)
                .where(Delivery.status == "sending", Delivery.lease_until < now)
                .values(status="uncertain", lease_until=None, available_at=now)
            )
            delivery = db.scalar(
                select(Delivery)
                .where(
                    or_(Delivery.status == "pending", Delivery.status == "uncertain"),
                    Delivery.available_at <= now,
                )
                .order_by(Delivery.available_at, Delivery.part)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if delivery is None:
                return False
            report = db.get(Report, delivery.report_id)
            assert report is not None
            channel = db.get(Channel, report.channel_id)
            assert channel is not None
            workspace = db.get(Workspace, channel.workspace_id)
            if not workspace or not workspace.active or not channel.active:
                delivery.available_at = now + timedelta(minutes=1)
                return False
            if db.scalar(
                select(Delivery.id)
                .where(
                    Delivery.report_id == delivery.report_id,
                    Delivery.part < delivery.part,
                    Delivery.status != "sent",
                )
                .limit(1)
            ):
                delivery.available_at = now + timedelta(seconds=10)
                return False
            uncertain = delivery.status == "uncertain"
            fence = identifier()
            delivery.fence = fence
            delivery.lease_until = now + timedelta(seconds=120)
            delivery.available_at = now + timedelta(seconds=120)
            if not uncertain:
                delivery.status = "sending"
                delivery.sending_at = now
                delivery.attempts += 1
            else:
                report.status = "uncertain"
            delivery_id, channel_id = delivery.id, channel.slack_id
            content, sending_at = delivery.text, delivery.sending_at or now
            bot_id = workspace.bot_id
            encrypted_token = workspace.encrypted_token
        try:
            client: Any = self.client_factory(
                crypto(self.settings).decrypt(encrypted_token.encode()).decode()
            )
        except Exception:
            self._finish(
                delivery_id,
                fence,
                "uncertain" if uncertain else "failed",
                error="credential_unavailable",
                delay=300 if uncertain else 0,
            )
            return True
        if uncertain:
            try:
                cursor = None
                for _ in range(20):
                    history = client.conversations_history(
                        channel=channel_id,
                        oldest=str((sending_at - timedelta(seconds=30)).timestamp()),
                        latest=str((sending_at + timedelta(minutes=3)).timestamp()),
                        include_all_metadata=True,
                        limit=100,
                        cursor=cursor,
                    )
                    for message in history.get("messages", []):
                        marker = message.get("metadata", {}).get("event_payload", {})
                        if marker.get("delivery_id") == delivery_id and bot_id in {
                            message.get("bot_id"),
                            message.get("user"),
                        }:
                            self._finish(delivery_id, fence, "sent", slack_ts=message["ts"])
                            return True
                    cursor = history.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
            except Exception:
                pass
            self._finish(delivery_id, fence, "uncertain", error="confirmation_required", delay=300)
            return True
        try:
            response = client.chat_postMessage(
                channel=channel_id,
                text=content,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": content}}],
                metadata={
                    "event_type": "wordle_report",
                    "event_payload": {"delivery_id": delivery_id},
                },
                unfurl_links=False,
                unfurl_media=False,
            )
            if not response.get("ts"):
                raise TimeoutError
            self._finish(delivery_id, fence, "sent", slack_ts=response["ts"])
        except SlackApiError as error:
            code = str(error.response.get("error", "slack_error"))
            if error.response.status_code == 429:
                try:
                    delay = max(1, min(3600, float(error.response.headers.get("Retry-After", 60))))
                except ValueError, TypeError:
                    delay = 60
                with Session(self.engine) as db:
                    attempts = db.get(Delivery, delivery_id)
                    exhausted = attempts is not None and attempts.attempts >= 5
                self._finish(
                    delivery_id,
                    fence,
                    "failed" if exhausted else "pending",
                    error="ratelimited",
                    delay=delay,
                )
            elif code in {
                "invalid_auth",
                "token_revoked",
                "missing_scope",
                "not_in_channel",
                "channel_not_found",
                "is_archived",
                "invalid_blocks",
                "msg_too_long",
            }:
                self._finish(delivery_id, fence, "failed", error=code)
            else:
                self._finish(
                    delivery_id, fence, "uncertain", error="confirmation_required", delay=30
                )
        except Exception:
            self._finish(delivery_id, fence, "uncertain", error="confirmation_required", delay=30)
        return True
