"""Application behavior, independent of HTTP and database implementations."""

from datetime import UTC, date, datetime
from typing import Any

from wordle_bot.config import Settings
from wordle_bot.domain import parse_wordle, reaction_for, render_report, reporting_week
from wordle_bot.repositories.interfaces import StatsRepository
from wordle_bot.slack_client import SlackGateway


class WordleService:
    def __init__(
        self, settings: Settings, repository: StatsRepository, slack: SlackGateway
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.slack = slack

    def record_message(self, payload: dict[str, Any]) -> None:
        result = parse_wordle(payload["text"])
        if result is None:
            return
        day = (
            datetime.fromtimestamp(float(payload["ts"]), UTC)
            .astimezone(self.settings.timezone)
            .date()
        )
        self.repository.record(
            self.settings.slack_team_id, self.settings.slack_channel_id, payload["user"], day
        )
        self.slack.react(self.settings.slack_channel_id, payload["ts"], reaction_for(result))

    def report(self, day: date) -> list[str]:
        start, end = reporting_week(day)
        counts = self.repository.counts(
            self.settings.slack_team_id, self.settings.slack_channel_id, start, end
        )
        return render_report(day, counts)
