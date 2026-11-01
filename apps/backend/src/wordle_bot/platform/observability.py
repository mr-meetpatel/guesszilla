"""Bounded, best-effort redacted log sink; canonical audits remain transactional."""

import atexit
import logging
import queue
import re
import threading
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.platform.models import Event
from wordle_bot.repositories.postgres import create_db_engine


class EventLogHandler(logging.Handler):
    def __init__(self, engine: Engine, secrets: list[str]) -> None:
        super().__init__(level=logging.INFO)
        self.engine = engine
        self.secrets = [secret for secret in secrets if secret]
        self.pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self.stopping = threading.Event()
        self.dropped = 0
        self.thread = threading.Thread(target=self._run, name="redacted-event-sink", daemon=True)
        self.thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("wordle_bot"):
            return
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[redacted]")
        message = re.sub(r"xox\w-[\w-]+|postgres(?:ql)?(?:\+\w+)?://\S+", "[redacted]", message)
        details = {
            name: str(getattr(record, name))[:128]
            for name in ("job_id", "attempt", "error_code", "worker_id")
            if hasattr(record, name)
        }
        self.record("log", message[:120], record.levelname, details)

    def record(
        self,
        category: str,
        action: str,
        level: str,
        details: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        try:
            self.pending.put_nowait(
                {
                    "category": category,
                    "action": action,
                    "level": level,
                    "details": details,
                    "request_id": request_id,
                }
            )
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self.stopping.is_set() or not self.pending.empty():
            try:
                item = self.pending.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                batch = [item]
                while len(batch) < 100:
                    try:
                        batch.append(self.pending.get_nowait())
                    except queue.Empty:
                        break
                with Session(self.engine) as db, db.begin():
                    db.add_all(Event(**entry) for entry in batch)
            except Exception:
                # Never recursively log a failed logging write or block the request path.
                self.dropped += len(batch)

    def shutdown(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=3)
        self.engine.dispose()


def install_sink(settings: Settings) -> EventLogHandler:
    dedicated = settings.model_copy(update={"db_pool_size": 1})
    sink = EventLogHandler(
        create_db_engine(dedicated),
        [
            settings.database_url.get_secret_value(),
            settings.jwt_secret_key.get_secret_value(),
            settings.slack_bot_token.get_secret_value(),
            settings.slack_signing_secret.get_secret_value(),
            settings.slack_client_secret.get_secret_value(),
            settings.send_stats_password.get_secret_value(),
        ],
    )
    logging.getLogger("wordle_bot").addHandler(sink)
    atexit.register(sink.shutdown)
    return sink
