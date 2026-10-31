from datetime import date

import pytest
from conftest import wordle_text

from wordle_bot.domain import (
    ParticipationCount,
    parse_wordle,
    reaction_for,
    render_report,
    reporting_week,
)


@pytest.mark.parametrize(
    "score,reaction",
    [
        ("1", "fire"),
        ("2", "fire"),
        ("3", "tada"),
        ("4", "tada"),
        ("5", "tada"),
        ("6", "tada"),
        ("X", "thumbsup"),
    ],
)
@pytest.mark.parametrize("emoji", [True, False])
def test_shares(score, reaction, emoji):
    result = parse_wordle(wordle_text(score, emoji=emoji, hard=True))
    assert result is not None
    assert result.puzzle == 1234
    assert reaction_for(result) == reaction


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "Wordle 123 0/6",
        "Wordle 123 7/6",
        "Wordle 123 2/5\n\n🟩🟩🟩🟩🟩",
        "Wordle 123 2/6\n\n🟩🟩🟩🟩🟩",
        "Wordle 123 1/6\n\n🟩🟩🟩🟩",
        "Wordle 123 1/6\n\n🟩🟩🟩🟩🟩extra",
    ],
)
def test_invalid_shares(text):
    assert parse_wordle(text) is None


@pytest.mark.parametrize(
    "today,start,end",
    [
        (date(2026, 10, 4), date(2026, 9, 28), date(2026, 10, 2)),
        (date(2026, 10, 5), date(2026, 10, 5), date(2026, 10, 9)),
        (date(2027, 1, 1), date(2026, 12, 28), date(2027, 1, 1)),
    ],
)
def test_reporting_week(today, start, end):
    assert reporting_week(today) == (start, end)


def test_report_empty_sorting_and_chunking():
    today = date(2026, 10, 2)
    assert "No weekday participation" in render_report(today, [])[0]
    counts = [
        ParticipationCount("U003", 1),
        ParticipationCount("U002", 5),
        ParticipationCount("U001", 5),
    ]
    report = render_report(today, counts)[0]
    assert report.index("*Leaderboard*") < report.index("<@U001>")
    assert report.index("*Remaining Players List*") < report.index("<@U003>")
    assert "There are 2 royalties this week" in report
    assert "<@U003> (1/5)" in report
    chunks = render_report(today, [ParticipationCount(f"U{i:08}", 1) for i in range(1000)])
    assert len(chunks) > 1
    assert all(len(chunk) <= 2800 for chunk in chunks)
    assert sum(chunk.count("<@") for chunk in chunks) == 1000
