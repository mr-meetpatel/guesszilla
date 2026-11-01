import json
import subprocess
import sys
from unittest.mock import Mock

from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse

from wordle_bot.repositories.postgres import ClaimedJob
from wordle_bot.runtime import combined_commands, supervise
from wordle_bot.slack_client import SlackClient
from wordle_bot.worker import Worker


def slack_error(code, status=200, headers=None):
    return SlackApiError(
        "error",
        SlackResponse(
            client=None,
            http_verb="POST",
            api_url="test",
            req_args={},
            data={"ok": False, "error": code},
            headers=headers or {},
            status_code=status,
        ),
    )


def test_rate_limit_and_permanent_failure(settings):
    repository, service = Mock(), Mock()
    worker = Worker(settings, repository, service)
    job = ClaimedJob(1, "message", {}, 1, "token")
    service.record_message.side_effect = slack_error("ratelimited", 429, {"Retry-After": "42"})
    worker.process(job)
    repository.fail.assert_called_with(job, "ratelimited", 42, permanent=False)
    service.record_message.side_effect = slack_error("missing_scope")
    worker.process(job)
    assert repository.fail.call_args.kwargs["permanent"] is True
    repository.complete.assert_not_called()


def test_complete_and_lost_lease(settings):
    repository, service = Mock(), Mock()
    service.report.return_value = ["first", "second"]
    repository.renew.side_effect = [True, False]
    worker = Worker(settings, repository, service)
    job = ClaimedJob(1, "report", {"day": "2026-10-02"}, 1, "token")
    worker.process(job)
    service.slack.post.assert_called_once_with("C123", "first")
    repository.complete.assert_not_called()
    message = ClaimedJob(2, "message", {}, 1, "token")
    worker.process(message)
    repository.complete.assert_called_once_with(message)


def test_existing_reaction_is_success():
    slack = SlackClient("test")
    slack.client = Mock()
    slack.client.reactions_add.side_effect = slack_error("already_reacted")
    slack.react("C123", "123.456", "fire")


def test_combined_includes_scheduler_only_in_platform_mode():
    legacy = combined_commands()
    platform = combined_commands(admin_panel_enabled=True)
    assert [command[-2] for command in legacy] == ["serve", "worker"]
    assert [command[-2] for command in platform] == ["serve", "worker", "scheduler"]
    assert all(command[-1] == "--skip-migrate" for command in platform)


def test_supervisor_stops_sibling_when_child_exits(tmp_path):
    pid_file = tmp_path / "pid"
    sleeper = [
        sys.executable,
        "-c",
        "import os,time,pathlib; "
        f"pathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid())); time.sleep(60)",
    ]
    failing = [sys.executable, "-c", "import time; time.sleep(0.5); raise SystemExit(1)"]
    assert supervise([sleeper, failing], shutdown_timeout=1) == 1
    pid = int(pid_file.read_text())
    check = subprocess.run(
        [sys.executable, "-c", f"import os,sys; os.kill({pid}, 0)"], capture_output=True
    )
    assert check.returncode != 0
