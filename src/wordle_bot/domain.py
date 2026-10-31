"""Pure parsing, reaction selection, calendar rules, and report rendering."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

HEADER = re.compile(r"Wordle\s+(?P<puzzle>\d[\d,]*)\s+(?P<score>[1-6X])/6(?P<hard>\*)?")
SQUARE = re.compile(
    r"[⬛⬜🟨🟩🟧🟦]|:(?:white_large_square|black_large_square|large_yellow_square|"
    r"large_green_square|large_orange_square|large_blue_square):"
)


@dataclass(frozen=True)
class WordleResult:
    puzzle: int
    guesses: int | None


@dataclass(frozen=True)
class ParticipationCount:
    user_id: str
    days: int


def parse_wordle(text: str) -> WordleResult | None:
    lines = text.strip().splitlines()
    if not lines or (match := HEADER.fullmatch(lines[0].strip())) is None:
        return None
    grid = [line.strip() for line in lines[1:] if line.strip()]
    score = match["score"]
    expected_rows = 6 if score == "X" else int(score)
    if len(grid) != expected_rows:
        return None
    for line in grid:
        if len(SQUARE.findall(line)) != 5 or SQUARE.sub("", line).strip():
            return None
    try:
        puzzle = int(match["puzzle"].replace(",", ""))
    except ValueError:
        return None
    return WordleResult(puzzle=puzzle, guesses=None if score == "X" else int(score))


def reaction_for(result: WordleResult) -> str:
    if result.guesses is None:
        return "thumbsup"
    return "fire" if result.guesses <= 2 else "tada"


def reporting_week(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=4)


def render_report(today: date, counts: list[ParticipationCount]) -> list[str]:
    monday, friday = reporting_week(today)
    # Slack resolves the mention to the user's display name; keep a deterministic
    # alphabetical order based on the stable user identifier available in storage.
    ordered = sorted(counts, key=lambda count: count.user_id.casefold())
    leaderboard = [count for count in ordered if count.days == 5]
    remaining = [count for count in ordered if count.days < 5]
    royalties = len(leaderboard)
    royalty_word = "royalty" if royalties == 1 else "royalties"
    lines = [
        "<!channel>",
        "",
        f"Stats for Attempted WORDLE for the week of {monday:%d %b} to {friday:%d %b}",
        f"There {'is' if royalties == 1 else 'are'} {royalties} {royalty_word} this week.. :crown:",
        "Check your stats below :tada:",
    ]
    if leaderboard:
        lines.extend(["", "*Leaderboard* 🏆"])
        lines.extend(f"- <@{count.user_id}>" for count in leaderboard)
    if remaining:
        lines.extend(["", "*Remaining Players List* 👏"])
        lines.extend(f"- <@{count.user_id}> ({count.days}/5)" for count in remaining)
    if not ordered:
        lines.extend(["", "No weekday participation recorded this week yet."])
    # Each message is a single mrkdwn section, under Slack's 3,000-character limit.
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
