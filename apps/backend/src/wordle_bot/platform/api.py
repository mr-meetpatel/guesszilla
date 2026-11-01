"""JWT-protected admin API. Responses expose explicit safe fields only."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from flask import abort, g, jsonify, request
from flask_jwt_extended import get_jwt
from flask_smorest import Api, Blueprint
from marshmallow import Schema, fields
from pydantic import ValidationError
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.platform.auth import audit, passwords, protected, require_password
from wordle_bot.platform.models import (
    Admin,
    AdminSession,
    Channel,
    Delivery,
    Event,
    LoginBucket,
    OAuthState,
    Occurrence,
    Participant,
    Report,
    Schedule,
    Workspace,
    utcnow,
)
from wordle_bot.platform.oauth import SlackInstallations
from wordle_bot.platform.reports import ReportService, report_dict
from wordle_bot.platform.rules import ReportRules, next_occurrence, resolve_window
from wordle_bot.platform.scheduler import schedule_values
from wordle_bot.platform.schemas import (
    ChannelList,
    ChannelResponse,
    ReportList,
    ReportResponse,
    SnapshotResponse,
    WorkspaceList,
)
from wordle_bot.repositories.models import Heartbeat, Job
from wordle_bot.repositories.postgres import PostgresRepository


class Envelope(Schema):
    data = fields.Raw()
    cursor = fields.Integer(allow_none=True)
    total = fields.Integer()


class Payload(Schema):
    class Meta:
        unknown = "include"


def channel_dict(channel: Channel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "workspace_id": channel.workspace_id,
        "slack_id": channel.slack_id,
        "name": channel.name,
        "active": channel.active,
        "rules": channel.rules,
        "version": channel.version,
        "has_password": bool(channel.password_hash),
        "roster_synced_at": channel.roster_synced_at.isoformat()
        if channel.roster_synced_at
        else None,
    }


def schedule_dict(schedule: Schedule) -> dict[str, Any]:
    return {
        key: getattr(schedule, key)
        for key in (
            "id",
            "channel_id",
            "name",
            "frequency",
            "weekdays",
            "wall_time",
            "timezone",
            "window",
            "rolling_days",
            "paused",
            "version",
        )
    } | {"next_at": schedule.next_at.isoformat()}


def register_api(app: Any, settings: Settings, engine: Engine) -> None:
    app.config.update(API_TITLE="Wordle Admin", API_VERSION="v1", OPENAPI_VERSION="3.0.3")
    api = Api(app)
    app.extensions["admin_api"] = api
    api.spec.components.security_scheme(
        "cookieJwt", {"type": "apiKey", "in": "cookie", "name": "access_token_cookie"}
    )
    api.spec.options["security"] = [{"cookieJwt": []}]
    blueprint = Blueprint(
        "admin", __name__, url_prefix="/api/v1", description="Super-admin operations"
    )
    reports = ReportService(settings, engine)
    installations = SlackInstallations(settings, engine)
    installations.register(app)

    def body() -> dict[str, Any]:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(422, description="Expected a JSON object")
        return value

    def page() -> tuple[int, int]:
        try:
            return max(0, int(request.args.get("cursor", 0))), min(
                200, max(1, int(request.args.get("limit", 50)))
            )
        except ValueError:
            abort(422, description="Invalid pagination")

    def get_channel(db: Session, channel_id: str) -> Channel:
        channel = db.get(Channel, channel_id)
        if not channel:
            abort(404, description="Channel not found")
        return channel

    def validate_schedule_window(channel: Channel, values: dict[str, Any]) -> None:
        if values["paused"]:
            return
        rules = ReportRules(**channel.rules)
        start, end = resolve_window(
            values["next_at"].astimezone(ZoneInfo(rules.timezone)).date(),
            values["window"],
            values["rolling_days"],
            rules,
        )
        eligible = sum(
            (start + timedelta(days=offset)).weekday() in rules.weekdays
            for offset in range((end - start).days + 1)
        )
        if rules.threshold > eligible:
            abort(422, description="Royalty threshold exceeds eligible days in this window")

    @blueprint.get("/overview")
    @protected
    @blueprint.response(200, Envelope)
    def overview() -> dict[str, Any]:
        with Session(engine) as db:
            heartbeat = list(db.scalars(select(Heartbeat)))
            states = dict(
                db.execute(select(Delivery.status, func.count()).group_by(Delivery.status)).all()
            )
            jobs = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
            stale = [
                h.worker_id for h in heartbeat if h.updated_at < utcnow() - timedelta(seconds=90)
            ]
            scheduler = any(
                h.worker_id == "scheduler" and h.worker_id not in stale for h in heartbeat
            )
            worker = any(h.worker_id != "scheduler" and h.worker_id not in stale for h in heartbeat)
            schedule_issues = db.scalar(
                select(func.count())
                .select_from(Occurrence)
                .where(
                    Occurrence.status.in_(["invalid", "missed"]),
                    Occurrence.scheduled_at >= utcnow() - timedelta(days=1),
                )
            )
            overdue = db.scalar(
                select(func.count())
                .select_from(Schedule)
                .where(
                    Schedule.paused.is_(False),
                    Schedule.next_at < utcnow() - timedelta(minutes=5),
                )
            )
            return {
                "data": {
                    "database": "healthy",
                    "worker": worker,
                    "scheduler": scheduler,
                    "workspaces": db.scalar(select(func.count()).select_from(Workspace)),
                    "deliveries": states,
                    "jobs": jobs,
                    "stale_processes": stale,
                    "oldest_pending": db.scalar(
                        select(func.min(Job.created_at)).where(Job.status == "pending")
                    ),
                    "alerts": (["Worker heartbeat missing"] if not worker else [])
                    + (["Scheduler heartbeat missing"] if not scheduler else [])
                    + (["Slack deliveries need confirmation"] if states.get("uncertain") else [])
                    + (["Background jobs failed"] if jobs.get("failed") else [])
                    + (
                        ["Recent schedule occurrences were missed or invalid"]
                        if schedule_issues
                        else []
                    )
                    + (["Scheduled work is overdue"] if overdue else []),
                }
            }

    @blueprint.get("/workspaces")
    @protected
    @blueprint.response(200, WorkspaceList)
    def workspaces() -> dict[str, Any]:
        with Session(engine) as db:
            return {
                "data": [
                    {
                        "id": w.id,
                        "name": w.name,
                        "active": w.active,
                        "connected": bool(w.encrypted_token),
                        "scopes": w.scopes,
                        "updated_at": w.updated_at.isoformat(),
                    }
                    for w in db.scalars(select(Workspace).order_by(Workspace.name))
                ]
            }

    @blueprint.post("/workspaces/connect")
    @protected
    @blueprint.response(200, Envelope)
    def connect() -> dict[str, Any]:
        return {"data": {"url": installations.start(get_jwt()["sid"])}}

    @blueprint.patch("/workspaces/<workspace_id>")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def workspace_edit(data: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        if type(data.get("active")) is not bool:
            abort(422, description="active must be a boolean")
        with Session(engine) as db, db.begin():
            workspace = db.scalar(
                select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            )
            if not workspace:
                abort(404)
            if data["active"] and not workspace.encrypted_token:
                abort(409, description="Reconnect this workspace through Slack OAuth first")
            workspace.active = data["active"]
            audit(
                db,
                "workspace.status_changed",
                actor=g.admin_id,
                workspace=workspace_id,
                details={"active": workspace.active},
            )
        return {"data": {"ok": True}}

    @blueprint.post("/workspaces/<workspace_id>/sync")
    @protected
    @blueprint.response(200, Envelope)
    def discover(workspace_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            if not db.get(Workspace, workspace_id):
                abort(404)
        PostgresRepository(engine).enqueue(
            "discover:" + workspace_id + ":" + str(int(utcnow().timestamp()) // 60),
            "workspace_sync",
            {"workspace_id": workspace_id},
        )
        return {"data": {"queued": True}}

    @blueprint.delete("/workspaces/<workspace_id>")
    @protected
    @blueprint.response(200, Envelope)
    def disconnect(workspace_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            workspace = db.scalar(
                select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            )
            if not workspace:
                abort(404)
            workspace.active, workspace.encrypted_token = False, ""
            channel_ids = select(Channel.id).where(Channel.workspace_id == workspace_id)
            db.execute(
                update(Schedule)
                .where(Schedule.channel_id.in_(channel_ids))
                .values(paused=True, version=Schedule.version + 1)
            )
            db.execute(
                update(Channel)
                .where(Channel.workspace_id == workspace_id)
                .values(active=False, version=Channel.version + 1)
            )
            in_flight = select(Delivery.report_id).where(
                Delivery.status.in_(["sending", "uncertain"])
            )
            cancellable = list(
                db.scalars(
                    select(Report.id).where(
                        Report.channel_id.in_(channel_ids),
                        Report.status.in_(["pending", "failed"]),
                        Report.id.not_in(in_flight),
                    )
                )
            )
            if cancellable:
                db.execute(
                    update(Delivery)
                    .where(
                        Delivery.report_id.in_(cancellable),
                        Delivery.status.in_(["pending", "failed"]),
                    )
                    .values(status="cancelled")
                )
                db.execute(
                    update(Report).where(Report.id.in_(cancellable)).values(status="cancelled")
                )
            audit(db, "workspace.disconnected", actor=g.admin_id, workspace=workspace_id)
        return {"data": {"ok": True}}

    @blueprint.get("/channels")
    @protected
    @blueprint.response(200, ChannelList)
    def channels() -> dict[str, Any]:
        with Session(engine) as db:
            query = select(Channel).order_by(Channel.name)
            if request.args.get("workspace_id"):
                query = query.where(Channel.workspace_id == request.args["workspace_id"])
            return {"data": [channel_dict(c) for c in db.scalars(query)]}

    @blueprint.patch("/channels/<channel_id>")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, ChannelResponse)
    def channel_edit(data: dict[str, Any], channel_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            channel = db.scalar(select(Channel).where(Channel.id == channel_id).with_for_update())
            if not channel:
                abort(404)
            if data.get("version") != channel.version:
                abort(409, description="Channel settings changed; reload before saving")
            try:
                rules = ReportRules(**data.get("rules", channel.rules))
            except ValidationError, TypeError:
                abort(422, description="Invalid reporting rules")
            channel.rules = rules.model_dump()
            if "active" in data:
                if type(data["active"]) is not bool:
                    abort(422)
                channel.active = data["active"]
            if data.get("password"):
                password = require_password(data["password"])
                channel.password_hash = passwords.hash(password)
            channel.version += 1
            audit(
                db,
                "channel.settings_changed",
                actor=g.admin_id,
                target=channel.id,
                workspace=channel.workspace_id,
                details={"version": channel.version, "rules": channel.rules},
            )
            result = channel_dict(channel)
        return {"data": result}

    @blueprint.get("/channels/<channel_id>/participants")
    @protected
    @blueprint.response(200, Envelope)
    def participants(channel_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            get_channel(db, channel_id)
            rows = db.scalars(select(Participant).where(Participant.channel_id == channel_id))
            return {
                "data": sorted(
                    [
                        {
                            "user_id": p.user_id,
                            "name": p.name,
                            "active": p.active,
                            "included": p.included,
                        }
                        for p in rows
                    ],
                    key=lambda p: str(p["name"]).casefold(),
                )
            }

    @blueprint.patch("/channels/<channel_id>/participants/<user_id>")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def participant_edit(data: dict[str, Any], channel_id: str, user_id: str) -> dict[str, Any]:
        if type(data.get("included")) is not bool:
            abort(422)
        with Session(engine) as db, db.begin():
            participant = db.get(Participant, (channel_id, user_id))
            if not participant:
                abort(404)
            participant.included = data["included"]
            audit(
                db,
                "roster.eligibility_changed",
                actor=g.admin_id,
                target=user_id,
                workspace=get_channel(db, channel_id).workspace_id,
                details={"included": participant.included},
            )
        return {"data": {"ok": True}}

    @blueprint.post("/channels/<channel_id>/sync")
    @protected
    @blueprint.response(200, Envelope)
    def sync(channel_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            get_channel(db, channel_id)
        PostgresRepository(engine).enqueue(
            "roster:" + channel_id + ":" + str(int(utcnow().timestamp()) // 60),
            "roster_sync",
            {"channel_id": channel_id},
        )
        return {"data": {"queued": True}}

    def dates() -> tuple[date, date]:
        try:
            return date.fromisoformat(request.args["start"]), date.fromisoformat(
                request.args["end"]
            )
        except KeyError, ValueError:
            abort(422, description="start and end must be ISO dates")

    @blueprint.get("/channels/<channel_id>/analytics")
    @protected
    @blueprint.response(200, SnapshotResponse)
    def analytics(channel_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            channel = get_channel(db, channel_id)
            return {"data": reports.snapshot(db, channel, *dates())}

    @blueprint.get("/reports")
    @protected
    @blueprint.response(200, ReportList)
    def report_list() -> dict[str, Any]:
        offset, limit = page()
        with Session(engine) as db:
            query = select(Report).order_by(Report.created_at.desc())
            if request.args.get("channel_id"):
                query = query.where(Report.channel_id == request.args["channel_id"])
            rows = list(db.scalars(query.offset(offset).limit(limit + 1)))
            return {
                "data": [report_dict(r) for r in rows[:limit]],
                "cursor": offset + limit if len(rows) > limit else None,
            }

    @blueprint.post("/channels/<channel_id>/reports")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, ReportResponse)
    def send(data: dict[str, Any], channel_id: str) -> dict[str, Any]:
        try:
            start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
        except KeyError, ValueError, TypeError:
            abort(422, description="Supply start and end dates")
        expected = data.get("expected_revision")
        if expected is not None and (
            type(expected) is not int or data.get("confirm_resend") is not True
        ):
            abort(422, description="Resend requires confirmation and the expected revision")
        return {
            "data": reports.request_report(
                channel_id,
                start,
                end,
                request.headers.get("Idempotency-Key", ""),
                g.admin_id,
                expected_revision=expected,
            )
        }

    @blueprint.get("/reports/<report_id>/deliveries")
    @protected
    @blueprint.response(200, Envelope)
    def deliveries(report_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            if not db.get(Report, report_id):
                abort(404)
            return {
                "data": [
                    {
                        "id": d.id,
                        "part": d.part,
                        "status": d.status,
                        "attempts": d.attempts,
                        "slack_ts": d.slack_ts,
                        "last_error": d.last_error,
                        "text": d.text,
                    }
                    for d in db.scalars(
                        select(Delivery)
                        .where(Delivery.report_id == report_id)
                        .order_by(Delivery.part)
                    )
                ]
            }

    @blueprint.post("/deliveries/<delivery_id>/reconcile")
    @protected
    @blueprint.response(200, Envelope)
    def reconcile(delivery_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            delivery = db.scalar(
                select(Delivery).where(Delivery.id == delivery_id).with_for_update()
            )
            if not delivery or delivery.status != "uncertain":
                abort(409, description="Only uncertain deliveries can be reconciled")
            delivery.available_at = utcnow()
            audit(db, "delivery.reconcile_requested", actor=g.admin_id, target=delivery_id)
        return {"data": {"queued": True}}

    @blueprint.post("/deliveries/<delivery_id>/retry")
    @protected
    @blueprint.response(200, Envelope)
    def delivery_retry(delivery_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            delivery = db.scalar(
                select(Delivery).where(Delivery.id == delivery_id).with_for_update()
            )
            if not delivery or delivery.status != "failed":
                abort(409, description="Only confirmed failed deliveries may be retried")
            delivery.status, delivery.attempts = "pending", 0
            delivery.available_at = utcnow()
            report = db.get(Report, delivery.report_id)
            assert report is not None
            report.status = "pending"
            audit(db, "delivery.retry_requested", actor=g.admin_id, target=delivery_id)
        return {"data": {"queued": True}}

    @blueprint.get("/schedules")
    @protected
    @blueprint.response(200, Envelope)
    def schedules() -> dict[str, Any]:
        with Session(engine) as db:
            query = select(Schedule).order_by(Schedule.next_at)
            if request.args.get("channel_id"):
                query = query.where(Schedule.channel_id == request.args["channel_id"])
            return {"data": [schedule_dict(s) for s in db.scalars(query)]}

    @blueprint.post("/channels/<channel_id>/schedules")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def add_schedule(data: dict[str, Any], channel_id: str) -> dict[str, Any]:
        try:
            values = schedule_values(data)
        except ValueError, TypeError:
            abort(422, description="Invalid schedule configuration")
        with Session(engine) as db, db.begin():
            channel = get_channel(db, channel_id)
            validate_schedule_window(channel, values)
            schedule = Schedule(channel_id=channel_id, **values)
            db.add(schedule)
            db.flush()
            audit(db, "schedule.created", actor=g.admin_id, target=schedule.id)
            result = schedule_dict(schedule)
        return {"data": result}

    @blueprint.patch("/schedules/<schedule_id>")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def edit_schedule(data: dict[str, Any], schedule_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            schedule = db.scalar(
                select(Schedule).where(Schedule.id == schedule_id).with_for_update()
            )
            if not schedule:
                abort(404)
            if data.get("version") != schedule.version:
                abort(409, description="Schedule changed; reload before saving")
            try:
                values = schedule_values(schedule_dict(schedule) | data)
            except ValueError, TypeError:
                abort(422, description="Invalid schedule configuration")
            validate_schedule_window(get_channel(db, schedule.channel_id), values)
            for name, value in values.items():
                setattr(schedule, name, value)
            schedule.version += 1
            audit(db, "schedule.changed", actor=g.admin_id, target=schedule.id)
            result = schedule_dict(schedule)
        return {"data": result}

    @blueprint.get("/schedules/<schedule_id>/occurrences")
    @protected
    @blueprint.response(200, Envelope)
    def occurrences(schedule_id: str) -> dict[str, Any]:
        with Session(engine) as db:
            schedule = db.get(Schedule, schedule_id)
            if not schedule:
                abort(404)
            upcoming = []
            at = schedule.next_at
            for _ in range(5):
                upcoming.append(at.isoformat())
                at = next_occurrence(
                    at, schedule.frequency, schedule.weekdays, schedule.wall_time, schedule.timezone
                )
            rows = db.scalars(
                select(Occurrence)
                .where(Occurrence.schedule_id == schedule_id)
                .order_by(Occurrence.scheduled_at.desc())
                .limit(100)
            )
            return {
                "data": {
                    "upcoming": upcoming,
                    "history": [
                        {
                            "id": o.id,
                            "scheduled_at": o.scheduled_at.isoformat(),
                            "status": o.status,
                            "report_id": o.report_id,
                        }
                        for o in rows
                    ],
                }
            }

    @blueprint.get("/jobs")
    @protected
    @blueprint.response(200, Envelope)
    def jobs() -> dict[str, Any]:
        offset, limit = page()
        with Session(engine) as db:
            query = select(Job).order_by(Job.id.desc())
            if request.args.get("status"):
                query = query.where(Job.status == request.args["status"])
            rows = list(db.scalars(query.offset(offset).limit(limit + 1)))
            return {
                "data": [
                    {
                        "id": j.id,
                        "kind": j.kind,
                        "status": j.status,
                        "attempts": j.attempts,
                        "last_error": j.last_error,
                        "created_at": j.created_at.isoformat(),
                        "lease_until": j.lease_until.isoformat() if j.lease_until else None,
                    }
                    for j in rows[:limit]
                ],
                "cursor": offset + limit if len(rows) > limit else None,
            }

    @blueprint.post("/jobs/<int:job_id>/retry")
    @protected
    @blueprint.response(200, Envelope)
    def retry_job(job_id: int) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not job or job.status != "failed" or job.kind == "report":
                abort(409, description="Report retries must use delivery controls")
            job.status, job.attempts, job.available_at = "pending", 0, utcnow()
            audit(db, "job.retry_requested", actor=g.admin_id, target=str(job_id))
        return {"data": {"queued": True}}

    @blueprint.get("/events")
    @protected
    @blueprint.response(200, Envelope)
    def events() -> dict[str, Any]:
        cursor, limit = page()
        with Session(engine) as db:
            query = select(Event).where(Event.id > cursor)
            for name in ("workspace_id", "category", "level", "target", "request_id"):
                if request.args.get(name):
                    query = query.where(getattr(Event, name) == request.args[name])
            if request.args.get("search"):
                query = query.where(Event.action.ilike("%" + request.args["search"][:120] + "%"))
            for name, op in (("start", "ge"), ("end", "le")):
                if request.args.get(name):
                    try:
                        boundary = datetime.fromisoformat(request.args[name])
                    except ValueError:
                        abort(422)
                    query = (
                        query.where(Event.created_at >= boundary)
                        if op == "ge"
                        else query.where(Event.created_at <= boundary)
                    )
            if request.args.get("tail") == "true" and cursor == 0:
                rows = list(
                    reversed(list(db.scalars(query.order_by(Event.id.desc()).limit(limit))))
                )
            else:
                rows = list(db.scalars(query.order_by(Event.id).limit(limit)))
            return {
                "data": [
                    {
                        "id": e.id,
                        "category": e.category,
                        "level": e.level,
                        "action": e.action,
                        "actor": e.actor,
                        "target": e.target,
                        "workspace_id": e.workspace_id,
                        "details": e.details,
                        "request_id": e.request_id,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in rows
                ],
                "cursor": rows[-1].id if rows else cursor,
            }

    @blueprint.get("/admins")
    @protected
    @blueprint.response(200, Envelope)
    def admins() -> dict[str, Any]:
        with Session(engine) as db:
            return {
                "data": [
                    {
                        "id": a.id,
                        "username": a.username,
                        "active": a.active,
                        "must_change_password": a.must_change_password,
                    }
                    for a in db.scalars(select(Admin).order_by(Admin.username))
                ]
            }

    @blueprint.post("/admins")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def add_admin(data: dict[str, Any]) -> dict[str, Any]:
        username, password = (
            str(data.get("username", "")).strip().casefold(),
            require_password(data.get("password")),
        )
        if not username or len(username) > 80:
            abort(422)
        with Session(engine) as db, db.begin():
            if db.scalar(select(Admin.id).where(Admin.username == username)):
                abort(409, description="Username already exists")
            admin = Admin(
                username=username, password_hash=passwords.hash(password), must_change_password=True
            )
            db.add(admin)
            db.flush()
            audit(db, "admin.created", actor=g.admin_id, target=admin.id)
            result = {"id": admin.id, "username": admin.username}
        return {"data": result}

    @blueprint.patch("/admins/<admin_id>")
    @protected
    @blueprint.arguments(Payload)
    @blueprint.response(200, Envelope)
    def edit_admin(data: dict[str, Any], admin_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            locked = list(db.scalars(select(Admin).order_by(Admin.id).with_for_update()))
            admin = next((a for a in locked if a.id == admin_id), None)
            if not admin:
                abort(404)
            if data.get("active") is False and admin.active and sum(a.active for a in locked) <= 1:
                abort(409, description="Cannot disable the last active super-admin")
            if "active" in data:
                if type(data["active"]) is not bool:
                    abort(422)
                admin.active = data["active"]
            if data.get("password"):
                password = require_password(data["password"])
                admin.password_hash = passwords.hash(password)
                admin.must_change_password = True
            db.execute(
                update(AdminSession).where(AdminSession.admin_id == admin_id).values(revoked=True)
            )
            audit(db, "admin.changed_sessions_revoked", actor=g.admin_id, target=admin_id)
        return {"data": {"ok": True}}

    @blueprint.get("/sessions")
    @protected
    @blueprint.response(200, Envelope)
    def sessions() -> dict[str, Any]:
        with Session(engine) as db:
            return {
                "data": [
                    {
                        "id": s.id,
                        "admin_id": s.admin_id,
                        "revoked": s.revoked,
                        "expires_at": s.expires_at.isoformat(),
                    }
                    for s in db.scalars(
                        select(AdminSession)
                        .where(AdminSession.expires_at > utcnow())
                        .order_by(AdminSession.created_at.desc())
                        .limit(200)
                    )
                ]
            }

    @blueprint.post("/sessions/<session_id>/revoke")
    @protected
    @blueprint.response(200, Envelope)
    def revoke(session_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            session = db.get(AdminSession, session_id)
            if not session:
                abort(404)
            session.revoked = True
            audit(db, "session.revoked", actor=g.admin_id, target=session_id)
        return {"data": {"ok": True}}

    @blueprint.get("/openapi.json")
    @protected
    def spec() -> Any:
        return jsonify(api.spec.to_dict())

    from wordle_bot.platform.maintenance import register_maintenance

    register_maintenance(blueprint, engine)
    api.register_blueprint(blueprint)


def prune(engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        db.execute(
            delete(Event).where(
                ((Event.category == "log") & (Event.created_at < utcnow() - timedelta(days=14)))
                | (
                    (Event.category.in_(["delivery", "job"]))
                    & (Event.created_at < utcnow() - timedelta(days=90))
                )
                | (
                    (Event.category == "audit")
                    & (Event.created_at < utcnow() - timedelta(days=365))
                )
            )
        )
        db.execute(delete(LoginBucket).where(LoginBucket.expires_at < utcnow()))
        db.execute(delete(OAuthState).where(OAuthState.expires_at < utcnow()))
