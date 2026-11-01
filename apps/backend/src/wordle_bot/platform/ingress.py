"""Tenant-scoped Slack ingress and background task handlers."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from flask import Response, jsonify
from slack_sdk.errors import SlackApiError
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.domain import parse_wordle
from wordle_bot.platform.auth import audit, passwords, verify_password
from wordle_bot.platform.models import Channel, Event, Participant, Workspace
from wordle_bot.platform.oauth import SlackInstallations
from wordle_bot.platform.reports import ReportService, crypto
from wordle_bot.platform.rules import ReportRules, resolve_window
from wordle_bot.repositories.models import Job, Participation
from wordle_bot.repositories.postgres import PostgresRepository


def managed_channel(engine: Engine, team: str, slack_channel: str) -> Channel | None:
    with Session(engine, expire_on_commit=False) as db:
        return db.scalar(
            select(Channel)
            .join(Workspace)
            .where(
                Workspace.id == team,
                Workspace.active.is_(True),
                Channel.active.is_(True),
                Channel.slack_id == slack_channel,
            )
        )


def command(settings: Settings, engine: Engine, data: Any) -> Response:
    channel = managed_channel(engine, data.get("team_id", ""), data.get("channel_id", ""))
    # Commands from another channel are allowed only when exactly one tracked destination exists.
    if channel is None:
        with Session(engine) as db:
            choices = list(
                db.scalars(
                    select(Channel)
                    .join(Workspace)
                    .where(
                        Workspace.id == data.get("team_id"),
                        Workspace.active.is_(True),
                        Channel.active.is_(True),
                    )
                )
            )
            channel = choices[0] if len(choices) == 1 else None
    if channel is None:
        return jsonify(response_type="ephemeral", text="Run this command in a tracked channel.")
    if data.get("command") != "/send-stats" or not verify_password(
        channel.password_hash, data.get("text", "").strip()
    ):
        return jsonify(response_type="ephemeral", text="Usage: /send-stats password")
    trigger = data.get("trigger_id", "")
    if not 1 <= len(trigger) <= 128:
        return Response(status=400)
    rules = ReportRules(**channel.rules)
    start, end = resolve_window(
        datetime.now(ZoneInfo(rules.timezone)).date(), "current_week", 7, rules
    )
    # Freeze only when the worker executes this report intent; never call Slack during ACK.
    PostgresRepository(engine).enqueue(
        f"command:{channel.workspace_id}:{trigger}",
        "report",
        {"channel_id": channel.id, "start": start.isoformat(), "end": end.isoformat()},
    )
    return jsonify(
        response_type="ephemeral",
        text="Report requested. Existing deliveries for this range are reused.",
    )


class PlatformTasks:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings, self.engine = settings, engine
        self.installations = SlackInstallations(settings, engine)

    def process(self, job: Any) -> None:
        self.installations.progress = lambda: PostgresRepository(self.engine).renew(
            job, self.settings.job_lease_seconds
        )
        if job.kind == "workspace_sync":
            self.installations.discover(job.payload["workspace_id"])
        elif job.kind == "roster_sync":
            self.installations.sync_roster(job.payload["channel_id"])
        elif job.kind == "report":
            channel_id = job.payload["channel_id"]
            with Session(self.engine) as db:
                channel = db.get(Channel, channel_id)
                if channel is None:
                    raise ValueError("Unknown channel")
                rules = ReportRules(**channel.rules)
            if "start" in job.payload:
                start, end = (
                    date.fromisoformat(job.payload["start"]),
                    date.fromisoformat(job.payload["end"]),
                )
            else:
                start, end = resolve_window(
                    date.fromisoformat(job.payload["day"]), "current_week", 7, rules
                )
            ReportService(self.settings, self.engine).request_report(
                channel_id, start, end, f"job:{job.id}", "slack-command"
            )
        elif job.kind == "message":
            self.record_message(job.payload)
        else:
            raise ValueError("Unknown task kind")
        if self.settings.persist_logs:
            with Session(self.engine) as db, db.begin():
                db.add(
                    Event(
                        category="job",
                        action="job.done",
                        target=str(job.id),
                        details={"kind": job.kind, "attempts": job.attempts},
                    )
                )

    def record_message(self, payload: dict[str, Any]) -> None:
        result = parse_wordle(payload["text"])
        if not result:
            return
        # Fetch a profile only for a new participant, without holding a database
        # transaction open during Slack's network request.
        name = payload["user"]
        with Session(self.engine) as db:
            channel = db.get(Channel, payload["channel_id"])
            workspace = db.get(Workspace, channel.workspace_id) if channel else None
            if not channel or not channel.active or not workspace or not workspace.active:
                return
            workspace_id = workspace.id
            missing = db.get(Participant, (channel.id, payload["user"])) is None
        if missing:
            user = self.installations.client(workspace_id).users_info(user=payload["user"])["user"]
            if user.get("is_bot") or user.get("is_app_user") or user.get("deleted"):
                return
            profile = user.get("profile", {})
            name = (profile.get("display_name") or profile.get("real_name") or name)[:160]
        with Session(self.engine) as db, db.begin():
            channel = db.get(Channel, payload["channel_id"])
            if not channel or not channel.active:
                return
            workspace = db.get(Workspace, channel.workspace_id)
            if not workspace or not workspace.active:
                return
            rules = ReportRules(**channel.rules)
            timestamp = datetime.fromtimestamp(float(payload["ts"]), UTC)
            day = timestamp.astimezone(ZoneInfo(rules.timezone)).date()
            db.execute(
                insert(Participant)
                .values(channel_id=channel.id, user_id=payload["user"], name=name)
                .on_conflict_do_nothing(
                    index_elements=[Participant.channel_id, Participant.user_id]
                )
            )
            db.execute(
                insert(Participation)
                .values(
                    team_id=workspace.id,
                    channel_id=channel.slack_id,
                    user_id=payload["user"],
                    day=day,
                    source_at=timestamp,
                )
                .on_conflict_do_nothing(constraint="uq_participation_day")
            )
            workspace_id, slack_id = workspace.id, channel.slack_id
        try:
            self.installations.client(workspace_id).reactions_add(
                channel=slack_id,
                timestamp=payload["ts"],
                name=rules.reactions.get(str(result.guesses) if result.guesses else "X", "tada"),
            )
        except SlackApiError as error:
            if error.response.get("error") != "already_reacted":
                raise


def import_legacy(settings: Settings, engine: Engine) -> None:
    if not all(
        (
            settings.slack_team_id,
            settings.slack_channel_id,
            settings.slack_bot_token.get_secret_value(),
            settings.send_stats_password.get_secret_value(),
        )
    ):
        raise ValueError("Legacy workspace configuration is incomplete")
    with Session(engine) as db, db.begin():
        workspace = db.get(Workspace, settings.slack_team_id)
        if not workspace:
            workspace = Workspace(
                id=settings.slack_team_id,
                name="Imported workspace",
                encrypted_token=crypto(settings)
                .encrypt(settings.slack_bot_token.get_secret_value().encode())
                .decode(),
            )
            db.add(workspace)
            db.flush()
        channel = db.scalar(
            select(Channel).where(
                Channel.workspace_id == workspace.id, Channel.slack_id == settings.slack_channel_id
            )
        )
        if not channel:
            channel = Channel(
                workspace_id=workspace.id,
                slack_id=settings.slack_channel_id,
                name="Imported Wordle channel",
                rules=ReportRules(timezone=settings.app_timezone).model_dump(),
                password_hash=passwords.hash(settings.send_stats_password.get_secret_value()),
            )
            db.add(channel)
            db.flush()
        for job in db.scalars(select(Job).where(Job.status.in_(["pending", "running", "failed"]))):
            if job.kind in {"message", "report"} and "channel_id" not in job.payload:
                job.payload = {**job.payload, "channel_id": channel.id}
        audit(db, "workspace.legacy_imported", workspace=workspace.id)
