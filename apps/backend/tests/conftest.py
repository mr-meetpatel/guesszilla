from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from wordle_bot.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr("postgresql://wordle:test@localhost/wordle_test"),
        slack_bot_token=SecretStr("xoxb-test"),
        slack_signing_secret=SecretStr("test-signing-secret"),
        slack_team_id="T123",
        slack_channel_id="C123",
        send_stats_password=SecretStr("password"),
    )


def wordle_text(score: str = "2", *, emoji: bool = False, hard: bool = False) -> str:
    row = ":large_green_square:" * 5 if emoji else "🟩" * 5
    rows = 6 if score == "X" else int(score)
    return f"Wordle 1,234 {score}/6{'*' if hard else ''}\n\n" + "\n".join([row] * rows)


def message_payload() -> dict:
    return {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev123",
        "event": {
            "type": "message",
            "channel": "C123",
            "user": "U123",
            "text": wordle_text(),
            "ts": str(datetime.now(UTC).timestamp()),
        },
    }
