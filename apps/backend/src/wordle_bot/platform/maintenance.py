"""Explicit, tenant-scoped historical data removal for super-admins."""

from typing import Any, cast

from flask import abort, g, request
from flask_smorest import Blueprint
from sqlalchemy import Engine, String, delete, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from wordle_bot.platform.auth import audit, protected, verify_password
from wordle_bot.platform.models import (
    Admin,
    Channel,
    Delivery,
    Event,
    Idempotency,
    Occurrence,
    Participant,
    Report,
    Schedule,
    Workspace,
)
from wordle_bot.repositories.models import Job, Participation


def register_maintenance(blueprint: Blueprint, engine: Engine) -> None:
    def payload() -> dict[str, Any]:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(422, description="Expected a JSON object")
        return value

    def confirmation(db: Session, expected: str) -> None:
        data = payload()
        admin = db.get(Admin, g.admin_id)
        password = data.get("password", "")
        if (
            not isinstance(password, str)
            or not admin
            or not verify_password(admin.password_hash, password)
        ):
            abort(403, description="Current admin password is required")
        if data.get("confirmation") != expected:
            abort(422, description="Confirmation text does not match")

    @blueprint.post("/workspaces/<workspace_id>/cleanup")
    @protected
    @blueprint.response(200)
    def cleanup_workspace(workspace_id: str) -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            confirmation(db, workspace_id)
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Block new worker/scheduler/outbox claims for the short cleanup
            # transaction, then reject work already outside a transaction.
            db.execute(
                text(
                    "LOCK TABLE wordle_bot.jobs, wordle_bot.schedules, "
                    "wordle_bot.deliveries IN EXCLUSIVE MODE"
                )
            )
            workspace = db.scalar(
                select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            )
            if not workspace:
                abort(404)
            if workspace.active:
                abort(409, description="Pause the workspace before clearing its history")
            channels = select(Channel.id).where(Channel.workspace_id == workspace_id)
            report_ids = select(Report.id).where(Report.channel_id.in_(channels))
            job_scope = or_(
                Job.payload["channel_id"].astext.in_(channels),
                Job.payload["workspace_id"].astext == workspace_id,
                Job.delivery_key.startswith("event:" + workspace_id + ":"),
                Job.delivery_key.startswith("command:" + workspace_id + ":"),
            )
            if db.scalar(
                select(Job.id).where(job_scope, Job.status == "running").limit(1)
            ) or db.scalar(
                select(Delivery.id)
                .where(
                    Delivery.report_id.in_(report_ids),
                    Delivery.status.in_(["sending", "uncertain"]),
                )
                .limit(1)
            ):
                abort(
                    409,
                    description=(
                        "Wait for running jobs and resolve sending/uncertain "
                        "deliveries before cleanup"
                    ),
                )
            counts = {}
            for name, statement in (
                (
                    "events",
                    delete(Event).where(
                        or_(
                            Event.workspace_id == workspace_id,
                            Event.target.in_(select(Job.id.cast(String)).where(job_scope)),
                            Event.target.in_(
                                select(Delivery.id).where(Delivery.report_id.in_(report_ids))
                            ),
                            Event.target.in_(report_ids),
                            Event.target.in_(channels),
                            Event.target.in_(
                                select(Schedule.id).where(Schedule.channel_id.in_(channels))
                            ),
                        )
                    ),
                ),
                ("jobs", delete(Job).where(job_scope)),
                (
                    "occurrences",
                    delete(Occurrence).where(
                        Occurrence.schedule_id.in_(
                            select(Schedule.id).where(Schedule.channel_id.in_(channels))
                        )
                    ),
                ),
                ("idempotency", delete(Idempotency).where(Idempotency.report_id.in_(report_ids))),
                ("deliveries", delete(Delivery).where(Delivery.report_id.in_(report_ids))),
                ("reports", delete(Report).where(Report.channel_id.in_(channels))),
                ("participants", delete(Participant).where(Participant.channel_id.in_(channels))),
                (
                    "participation",
                    delete(Participation).where(Participation.team_id == workspace_id),
                ),
            ):
                counts[name] = cast(CursorResult[Any], db.execute(statement)).rowcount
            db.execute(
                update(Schedule)
                .where(Schedule.channel_id.in_(channels))
                .values(paused=True, version=Schedule.version + 1)
            )
            db.execute(
                update(Channel)
                .where(Channel.workspace_id == workspace_id)
                .values(roster_synced_at=None)
            )
            audit(
                db,
                "workspace.history_cleared",
                actor=g.admin_id,
                workspace=workspace_id,
                details=counts,
            )
        return {"data": {"deleted": counts}}

    @blueprint.post("/maintenance/events/cleanup")
    @protected
    @blueprint.response(200)
    def cleanup_events() -> dict[str, Any]:
        with Session(engine) as db, db.begin():
            confirmation(db, "DELETE EVENTS")
            deleted = cast(CursorResult[Any], db.execute(delete(Event))).rowcount
            audit(db, "events.cleared", actor=g.admin_id, details={"deleted": deleted})
        return {"data": {"deleted": deleted}}
