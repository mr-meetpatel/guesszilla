"""Short transactions, conflict-safe participation, and a durable leased queue."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, and_, create_engine, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.domain import ParticipationCount
from wordle_bot.repositories.models import Heartbeat, Job, Participation

MAX_ATTEMPTS = 5


def create_db_engine(settings: Settings, *, migration: bool = False) -> Engine:
    secret = settings.migration_database_url if migration else None
    url = make_url((secret or settings.database_url).get_secret_value()).set(
        drivername="postgresql+psycopg"
    )
    return create_engine(
        url,
        pool_size=1 if migration else settings.db_pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        pool_timeout=1,
        connect_args={"connect_timeout": 2, "options": "-c statement_timeout=2000"},
        hide_parameters=True,
    )


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    lease_token: str


class PostgresRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, team: str, channel: str, user: str, day: date) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(Participation)
                .values(team_id=team, channel_id=channel, user_id=user, day=day)
                .on_conflict_do_nothing(constraint="uq_participation_day")
            )

    def counts(self, team: str, channel: str, start: date, end: date) -> list[ParticipationCount]:
        query = (
            select(Participation.user_id, func.count())
            .where(
                Participation.team_id == team,
                Participation.channel_id == channel,
                Participation.day.between(start, end),
                func.extract("isodow", Participation.day) <= 5,
            )
            .group_by(Participation.user_id)
        )
        with self.engine.connect() as connection:
            return [ParticipationCount(user, days) for user, days in connection.execute(query)]

    def enqueue(self, key: str, kind: str, payload: dict[str, Any]) -> bool:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            result = connection.execute(
                insert(Job)
                .values(
                    delivery_key=key,
                    kind=kind,
                    payload=payload,
                    status="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[Job.delivery_key])
                .returning(Job.id)
            )
            return result.scalar_one_or_none() is not None

    def claim(self, lease_seconds: int) -> ClaimedJob | None:
        now = datetime.now(UTC)
        with Session(self.engine) as session, session.begin():
            # A worker that died during its final attempt must not strand the job forever.
            session.execute(
                update(Job)
                .where(
                    Job.status == "running",
                    Job.lease_until < now,
                    Job.attempts >= MAX_ATTEMPTS,
                )
                .values(
                    status="failed", lease_token=None, lease_until=None, last_error="lease_expired"
                )
            )
            job = session.scalar(
                select(Job)
                .where(
                    Job.attempts < MAX_ATTEMPTS,
                    or_(
                        and_(Job.status == "pending", Job.available_at <= now),
                        and_(Job.status == "running", Job.lease_until < now),
                    ),
                )
                .order_by(Job.available_at, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            token = str(uuid4())
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.lease_token = token
            return ClaimedJob(job.id, job.kind, dict(job.payload), job.attempts, token)

    def renew(self, job: ClaimedJob, lease_seconds: int) -> bool:
        with self.engine.begin() as connection:
            return (
                connection.execute(
                    update(Job)
                    .where(
                        Job.id == job.id,
                        Job.status == "running",
                        Job.lease_token == job.lease_token,
                    )
                    .values(lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds))
                    .returning(Job.id)
                ).scalar_one_or_none()
                is not None
            )

    def complete(self, job: ClaimedJob) -> None:
        self._finish(job, "done", None, 0)

    def fail(self, job: ClaimedJob, code: str, delay: float, *, permanent: bool = False) -> None:
        self._finish(
            job, "failed" if permanent or job.attempts >= MAX_ATTEMPTS else "pending", code, delay
        )

    def _finish(self, job: ClaimedJob, status: str, code: str | None, delay: float) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.lease_token == job.lease_token,
                )
                .values(
                    status=status,
                    lease_until=None,
                    lease_token=None,
                    last_error=code,
                    available_at=datetime.now(UTC) + timedelta(seconds=delay),
                )
            )

    def heartbeat(self, worker_id: str) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            connection.execute(
                insert(Heartbeat)
                .values(worker_id=worker_id, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[Heartbeat.worker_id], set_={"updated_at": now}
                )
            )

    def healthy(self, max_age: int, worker_id: str | None = None) -> bool:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            query = select(Heartbeat.worker_id).where(
                Heartbeat.updated_at >= datetime.now(UTC) - timedelta(seconds=max_age)
            )
            if worker_id:
                query = query.where(Heartbeat.worker_id == worker_id)
            return connection.execute(query.limit(1)).first() is not None

    def jobs(self, status: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(Job.id, Job.kind, Job.status, Job.attempts, Job.last_error)
                .where(Job.status == status)
                .order_by(Job.id)
                .limit(100)
            )
            return [dict(row._mapping) for row in rows]

    def retry(self, job_id: int) -> bool:
        with self.engine.begin() as connection:
            return (
                connection.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "failed")
                    .values(
                        status="pending",
                        attempts=0,
                        last_error=None,
                        available_at=datetime.now(UTC),
                    )
                    .returning(Job.id)
                ).scalar_one_or_none()
                is not None
            )
