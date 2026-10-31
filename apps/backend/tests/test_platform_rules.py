from datetime import UTC, date, datetime

import pytest

from wordle_bot.platform.rules import ReportRules, next_occurrence, render_snapshot, resolve_window


def test_custom_week_and_rolling_windows():
    rules = ReportRules(timezone="Asia/Kolkata", weekdays=[0, 1, 2, 3, 4], threshold=3)
    assert resolve_window(date(2026, 10, 31), "current_week", 7, rules) == (
        date(2026, 10, 26),
        date(2026, 10, 30),
    )
    assert resolve_window(date(2026, 10, 31), "previous_week", 7, rules)[0] == date(2026, 10, 19)
    assert resolve_window(date(2026, 10, 31), "rolling", 7, rules)[0] == date(2026, 10, 25)


def test_report_sorts_names_and_includes_zero_players():
    snapshot = {
        "start": "2026-10-26",
        "end": "2026-10-30",
        "threshold": 3,
        "eligible_days": 5,
        "announce": False,
        "title": "Weekly Wordle",
        "players": [
            {"user_id": "U1", "name": "Zed", "days": 3},
            {"user_id": "U2", "name": "Amy", "days": 4},
            {"user_id": "U3", "name": "Bob", "days": 0},
        ],
    }
    text = "\n".join(render_snapshot(snapshot))
    assert text.index("<@U2>") < text.index("<@U1>")
    assert "<@U3> (0/5)" in text
    assert "2 royalties" in text
    assert "<!channel>" not in text


def test_scheduler_dst_gap_and_overlap():
    # Spring forward: nonexistent 02:30 moves to the first valid local instant.
    assert next_occurrence(
        datetime(2026, 3, 8, 5, tzinfo=UTC), "daily", [6], "02:30", "America/New_York"
    ) == datetime(2026, 3, 8, 7, tzinfo=UTC)
    # Fall back: use the first 01:30 only, never the repeated wall time.
    first = next_occurrence(
        datetime(2026, 11, 1, 4, tzinfo=UTC), "daily", [6], "01:30", "America/New_York"
    )
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_occurrence(first, "daily", [6], "01:30", "America/New_York").date() == date(
        2026, 11, 2
    )


def test_invalid_rules_rejected():
    with pytest.raises(ValueError):
        ReportRules(timezone="Invalid", weekdays=[0], threshold=1)
    with pytest.raises(ValueError):
        ReportRules(weekdays=[], threshold=0)
