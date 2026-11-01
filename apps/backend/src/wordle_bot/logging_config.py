import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in (
            "job_id",
            "attempt",
            "error_code",
            "worker_id",
            "request_id",
            "schedule_id",
            "status",
        ):
            if hasattr(record, name):
                fields[name] = str(getattr(record, name))
        if hasattr(record, "error_message"):
            fields["error_message"] = str(record.error_message)
        # Exception strings can contain DB credentials, Slack bodies, and tokens.
        if record.exc_info and record.exc_info[0]:
            fields["error_type"] = record.exc_info[0].__name__
        return json.dumps(fields)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
