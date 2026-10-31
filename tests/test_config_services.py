from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from conftest import wordle_text
from pydantic import ValidationError

from wordle_bot.config import Settings
from wordle_bot.services import WordleService


def test_env_precedence_and_timezone(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://u:p@localhost/test\nAPP_TIMEZONE=UTC\nPORT=8001\n")
    monkeypatch.setenv("PORT", "9000")
    settings = Settings(_env_file=env)
    assert settings.port == 9000
    assert settings.app_timezone == "UTC"


@pytest.mark.parametrize(
    "changes",
    [
        {"database_url": "sqlite:///test.db"},
        {"app_timezone": "invalid-zone"},
        {"db_backend": "supabase"},
        {
            "db_backend": "supabase",
            "database_url": "postgresql://u:p@localhost:6543/test?sslmode=require",
        },
    ],
)
def test_invalid_settings(changes):
    values = {"database_url": "postgresql://u:p@localhost/test", **changes}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_missing_slack_credentials():
    settings = Settings(_env_file=None, database_url="postgresql://u:p@localhost/test")
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        settings.require_slack()


def test_participation_uses_event_time_in_local_timezone(settings):
    repository, slack = Mock(), Mock()
    service = WordleService(settings, repository, slack)
    timestamp = str(datetime(2026, 10, 4, 19, 0, tzinfo=UTC).timestamp())
    service.record_message({"user": "U123", "text": wordle_text(), "ts": timestamp})
    repository.record.assert_called_once_with("T123", "C123", "U123", date(2026, 10, 5))
    slack.react.assert_called_once_with("C123", timestamp, "fire")
