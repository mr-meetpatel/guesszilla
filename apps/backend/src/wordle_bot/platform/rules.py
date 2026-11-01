"""Pure reporting and calendar rules shared by previews and immutable reports."""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReportRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "Asia/Kolkata"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    threshold: int = Field(default=5, ge=1, le=366)
    announce: bool = True
    title: str = Field(default="Stats for Attempted WORDLE", max_length=120)
    reactions: dict[str, str] = Field(
        default_factory=lambda: {
            "1": "fire",
            "2": "fire",
            "3": "tada",
            "4": "tada",
            "5": "tada",
            "6": "tada",
            "X": "thumbsup",
        }
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception:
            raise ValueError("Invalid IANA timezone") from None
        return value

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, value: list[int]) -> list[int]:
        if not value or any(day < 0 or day > 6 for day in value):
            raise ValueError("Choose at least one valid weekday")
        return sorted(set(value))

    @field_validator("reactions")
    @classmethod
    def valid_reactions(cls, values: dict[str, str]) -> dict[str, str]:
        if set(values) != {"1", "2", "3", "4", "5", "6", "X"} or any(
            not re.fullmatch(r"[a-z0-9_+\-]{1,64}", value) for value in values.values()
        ):
            raise ValueError("Provide a Slack emoji name for every result (1–6 and X)")
        return values


def resolve_window(anchor: date, window: str, days: int, rules: ReportRules) -> tuple[date, date]:
    if window == "rolling":
        if not 1 <= days <= 366:
            raise ValueError("Rolling windows must contain 1–366 days")
        return anchor - timedelta(days=days - 1), anchor
    if window not in {"current_week", "previous_week"}:
        raise ValueError("Invalid report window")
    monday = anchor - timedelta(days=anchor.weekday() + (7 if window == "previous_week" else 0))
    return monday + timedelta(days=min(rules.weekdays)), monday + timedelta(
        days=max(rules.weekdays)
    )


def next_occurrence(
    after: datetime,
    frequency: str,
    weekdays: list[int],
    wall_time: str,
    timezone: str,
) -> datetime:
    zone = ZoneInfo(timezone)
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", wall_time):
        raise ValueError("Schedule time must be HH:MM")
    wall = time.fromisoformat(wall_time)
    if wall.second or wall.microsecond or wall.tzinfo:
        raise ValueError("Schedule time must be HH:MM")
    local = after.astimezone(zone)
    for offset in range(15):
        day = local.date() + timedelta(days=offset)
        if frequency == "weekly" and day.weekday() not in weekdays:
            continue
        naive = datetime.combine(day, wall)
        for shift in range(181):
            candidate = (naive + timedelta(minutes=shift)).replace(tzinfo=zone, fold=0)
            utc = candidate.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None):
                continue
            if utc > after:
                return utc
            break
    raise ValueError("No occurrence found")


def render_snapshot(snapshot: dict[str, Any]) -> list[str]:
    start, end = date.fromisoformat(snapshot["start"]), date.fromisoformat(snapshot["end"])
    players = sorted(snapshot["players"], key=lambda p: (p["name"].casefold(), p["user_id"]))
    leaders = [p for p in players if p["days"] >= snapshot["threshold"]]
    others = [p for p in players if p["days"] < snapshot["threshold"]]
    lines = (["<!channel>", ""] if snapshot["announce"] else []) + [
        f"{snapshot['title']} for the week of {start:%d %b} to {end:%d %b}",
        f"There {'is' if len(leaders) == 1 else 'are'} {len(leaders)} "
        f"{'royalty' if len(leaders) == 1 else 'royalties'} this week.. :crown:",
        "Check your stats below :tada:",
    ]
    for title, group in (("Leaderboard 🏆", leaders), ("Remaining Players List 👏", others)):
        if group:
            lines += ["", f"*{title}*"]
            lines += [
                f"- <@{p['user_id']}>"
                + (f" ({p['days']}/{snapshot['eligible_days']})" if group is others else "")
                for p in group
            ]
    if not players:
        lines += ["", "No eligible participants in this roster."]
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 2800:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    return chunks
