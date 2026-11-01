"""Public, read-only component health without exposing deployment internals."""

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import Engine, case, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from wordle_bot.config import Settings
from wordle_bot.repositories.models import Heartbeat


def health_status(engine: Engine | None, settings: Settings) -> tuple[dict[str, Any], int]:
    now = datetime.now(UTC)
    result: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "status": "degraded",
        "backend": {"status": "up"},
        "database": {"status": "unknown", "latency_ms": None},
        "worker": {"status": "unknown", "heartbeat_age_seconds": None},
        "scheduler": {
            "status": "unknown" if settings.admin_panel_enabled else "not_applicable",
            "heartbeat_age_seconds": None,
        },
    }
    if engine is None:
        return result, 503
    started = perf_counter()
    database_ok = False
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar_one() != 1:
                result["database"]["status"] = "down"
                return result, 503
            database_ok = True
            result["database"] = {
                "status": "up",
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            }
            worker, scheduler = connection.execute(
                select(
                    func.max(case((Heartbeat.worker_id != "scheduler", Heartbeat.updated_at))),
                    func.max(case((Heartbeat.worker_id == "scheduler", Heartbeat.updated_at))),
                )
            ).one()
            for name, stamp in (("worker", worker), ("scheduler", scheduler)):
                if name == "scheduler" and not settings.admin_panel_enabled:
                    continue
                age = (
                    max(0.0, (now - stamp).total_seconds()) if isinstance(stamp, datetime) else None
                )
                result[name] = {
                    "status": "up"
                    if age is not None and age <= settings.heartbeat_max_age_seconds
                    else "down",
                    "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                }
    except SQLAlchemyError:
        if not database_ok:
            result["database"]["status"] = "down"
        return result, 503
    healthy = all(
        result[name]["status"] in {"up", "not_applicable"}
        for name in ("backend", "database", "worker", "scheduler")
    )
    result["status"] = "operational" if healthy else "degraded"
    return result, 200 if healthy else 503
