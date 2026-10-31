"""Transactional schedule cursor, occurrence ledger and outbox creation."""

import logging
import signal
import threading
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from werkzeug.exceptions import HTTPException

from wordle_bot.config import Settings
from wordle_bot.platform.models import Channel, Occurrence, Schedule, Workspace, utcnow
from wordle_bot.platform.reports import ReportService
from wordle_bot.platform.rules import ReportRules, next_occurrence, resolve_window
from wordle_bot.repositories.postgres import PostgresRepository


class Scheduler:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings, self.engine = settings, engine
        self.reports = ReportService(settings, engine)

    def tick(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        with Session(self.engine) as db, db.begin():
            schedule = db.scalar(
                select(Schedule)
                .where(Schedule.paused.is_(False), Schedule.next_at <= now)
                .order_by(Schedule.next_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not schedule:
                return False
            frequency = cast(Literal["daily", "weekly"], schedule.frequency)
            latest = schedule.next_at
            # Record all skipped occurrences, but materialize at most one report per recovery.
            for _ in range(370):
                following = next_occurrence(
                    latest, frequency, schedule.weekdays, schedule.wall_time, schedule.timezone
                )
                if following > now:
                    break
                db.add(Occurrence(schedule_id=schedule.id, scheduled_at=latest, status="missed"))
                latest = following
            else:
                schedule.paused = True
                return True
            schedule.next_at = following
            channel = db.get(Channel, schedule.channel_id)
            assert channel is not None
            workspace = db.get(Workspace, channel.workspace_id)
            occurrence = Occurrence(schedule_id=schedule.id, scheduled_at=latest, status="missed")
            db.add(occurrence)
            if (
                now - latest > timedelta(hours=24)
                or not channel.active
                or not workspace
                or not workspace.active
            ):
                return True
            rules = ReportRules(**channel.rules)
            start, end = resolve_window(
                latest.astimezone(ZoneInfo(rules.timezone)).date(),
                schedule.window,
                schedule.rolling_days,
                rules,
            )
            try:
                report = self.reports.create(
                    db,
                    channel.id,
                    start,
                    end,
                    f"schedule:{schedule.id}:{latest.isoformat()}",
                    "scheduler",
                )
                occurrence.report_id = report.id
                occurrence.status = (
                    "suppressed" if report.status in {"delivered", "cancelled"} else "queued"
                )
            except HTTPException:
                occurrence.status = "invalid"
            return True

    def run(self) -> None:
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *args: stop.set())
        signal.signal(signal.SIGINT, lambda *args: stop.set())
        repository = PostgresRepository(self.engine)
        logging.getLogger(__name__).info("Scheduler started")
        last_pruned = None
        while not stop.is_set():
            try:
                repository.heartbeat("scheduler")
                if last_pruned != utcnow().date():
                    from wordle_bot.platform.api import prune

                    prune(self.engine)
                    last_pruned = utcnow().date()
                if self.tick():
                    continue
            except Exception as error:
                logging.getLogger(__name__).error(
                    "Scheduler unavailable", extra={"error_code": type(error).__name__}
                )
            stop.wait(5)


def schedule_values(data: dict[str, Any]) -> dict[str, Any]:
    frequency = str(data.get("frequency", "weekly"))
    if frequency not in {"daily", "weekly"}:
        raise ValueError("Frequency must be daily or weekly")
    days = data.get("weekdays", [4])
    if (
        not isinstance(days, list)
        or not days
        or any(type(day) is not int or day not in range(7) for day in days)
    ):
        raise ValueError("Choose valid weekdays")
    timezone, wall_time = (
        str(data.get("timezone", "Asia/Kolkata")),
        str(data.get("wall_time", "17:00")),
    )
    ReportRules(timezone=timezone)
    window = str(data.get("window", "current_week"))
    if window not in {"current_week", "previous_week", "rolling"}:
        raise ValueError("Invalid report window")
    rolling_days = int(data.get("rolling_days", 7))
    if not 1 <= rolling_days <= 366:
        raise ValueError("Rolling days must be between 1 and 366")
    if type(data.get("paused", True)) is not bool:
        raise ValueError("paused must be a boolean")
    name = str(data.get("name", "Weekly Wordle")).strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Name must contain 1–120 characters")
    return {
        "name": name,
        "frequency": frequency,
        "weekdays": days,
        "wall_time": wall_time,
        "timezone": timezone,
        "window": window,
        "rolling_days": rolling_days,
        "paused": data.get("paused", True),
        "next_at": next_occurrence(
            utcnow(), cast(Literal["daily", "weekly"], frequency), days, wall_time, timezone
        ),
    }
