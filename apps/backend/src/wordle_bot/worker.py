"""Durable queue consumer; network operations never hold row locks."""

import logging
import os
import random
import signal
import threading
from datetime import date
from types import FrameType

from slack_sdk.errors import SlackApiError

from wordle_bot.config import Settings
from wordle_bot.repositories.postgres import ClaimedJob, PostgresRepository
from wordle_bot.services import WordleService

logger = logging.getLogger(__name__)
PERMANENT_SLACK_ERRORS = {
    "invalid_auth",
    "token_revoked",
    "account_inactive",
    "missing_scope",
    "channel_not_found",
    "not_in_channel",
    "is_archived",
    "message_not_found",
    "invalid_name",
}


class Worker:
    def __init__(
        self, settings: Settings, repository: PostgresRepository, service: WordleService
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.service = service
        self.worker_id = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")
        self.stop = threading.Event()

    def process(self, job: ClaimedJob) -> None:
        try:
            if self.settings.admin_panel_enabled:
                from wordle_bot.platform.ingress import PlatformTasks

                PlatformTasks(self.settings, self.repository.engine).process(job)
            elif job.kind == "message":
                self.service.record_message(job.payload)
            elif job.kind == "report":
                chunks = self.service.report(date.fromisoformat(job.payload["day"]))
                for chunk in chunks:
                    if not self.repository.renew(job, self.settings.job_lease_seconds):
                        return
                    self.repository.heartbeat(self.worker_id)
                    self.service.slack.post(self.settings.slack_channel_id, chunk)
            else:
                self.repository.fail(job, "unknown_job_kind", 0, permanent=True)
                return
            self.repository.complete(job)
            logger.info("Job completed", extra={"job_id": job.id, "attempt": job.attempts})
        except SlackApiError as error:
            code = str(error.response.get("error", "slack_error"))
            # Only log known error codes, never Slack response payloads.
            permanent = code in PERMANENT_SLACK_ERRORS
            if error.response.status_code == 429:
                try:
                    delay = max(1.0, float(error.response.headers.get("Retry-After", "30")))
                except ValueError, TypeError:
                    delay = 30.0
            else:
                delay = self.retry_delay(job.attempts)
            safe_code = (
                code if permanent or code in {"ratelimited", "internal_error"} else "slack_error"
            )
            self.repository.fail(job, safe_code, delay, permanent=permanent)
            logger.warning("Slack job failed", extra={"job_id": job.id, "error_code": safe_code})
        except Exception as error:
            self.repository.fail(job, type(error).__name__, self.retry_delay(job.attempts))
            logger.warning(
                "Job failed",
                extra={"job_id": job.id, "error_code": type(error).__name__},
                exc_info=True,
            )

    @staticmethod
    def retry_delay(attempt: int) -> float:
        return min(300.0, 2.0**attempt) + random.uniform(0, 1)

    def run(self) -> None:
        def shutdown(signum: int, frame: FrameType | None) -> None:
            self.stop.set()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        logger.info("Worker started", extra={"worker_id": self.worker_id})
        while not self.stop.is_set():
            try:
                self.repository.heartbeat(self.worker_id)
                if self.settings.admin_panel_enabled:
                    from wordle_bot.platform.reports import OutboxDispatcher

                    OutboxDispatcher(self.settings, self.repository.engine).tick()
                job = self.repository.claim(self.settings.job_lease_seconds)
                if job:
                    self.process(job)
                    continue
            except Exception as error:
                logger.error(
                    "Worker database unavailable",
                    extra={"error_code": type(error).__name__},
                    exc_info=True,
                )
            self.stop.wait(self.settings.worker_poll_seconds)
