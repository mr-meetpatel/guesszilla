from datetime import date
from typing import Any, Protocol

from wordle_bot.domain import ParticipationCount


class StatsRepository(Protocol):
    def record(self, team: str, channel: str, user: str, day: date) -> None: ...

    def counts(
        self, team: str, channel: str, start: date, end: date
    ) -> list[ParticipationCount]: ...


class JobQueue(Protocol):
    def enqueue(self, key: str, kind: str, payload: dict[str, Any]) -> bool: ...
