import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from conftest import message_payload
from sqlalchemy.exc import OperationalError

from wordle_bot.http import create_app


class Queue:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, key, kind, payload):
        if key in self.jobs:
            return False
        self.jobs[key] = (kind, payload)
        return True


def signed(client, settings, path, payload, *, form=False, age=0):
    body = urlencode(payload) if form else json.dumps(payload)
    stamp = str(int(time.time()) - age)
    signature = (
        "v0="
        + hmac.new(
            settings.slack_signing_secret.get_secret_value().encode(),
            f"v0:{stamp}:{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    return client.post(
        path,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
            "X-Slack-Request-Timestamp": stamp,
            "X-Slack-Signature": signature,
        },
    )


def test_signature_and_challenge(settings):
    client = create_app(settings, Queue()).test_client()
    assert client.post("/slack/events", json={}).status_code == 401
    assert signed(client, settings, "/slack/events", {}, age=301).status_code == 401
    result = signed(
        client, settings, "/slack/events", {"type": "url_verification", "challenge": "abc"}
    )
    assert result.json == {"challenge": "abc"}
    assert client.get("/send-stats").status_code == 405


def test_events_are_durable_and_deduplicated(settings):
    queue = Queue()
    client = create_app(settings, queue).test_client()
    payload = message_payload()
    for _ in range(2):
        assert signed(client, settings, "/slack/events", payload).status_code == 200
    assert len(queue.jobs) == 1
    kind, saved = next(iter(queue.jobs.values()))
    assert kind == "message"
    assert set(saved) == {"user", "ts", "text"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("subtype", "message_changed"),
        ("bot_id", "B123"),
        ("channel", "C999"),
        ("text", "hello"),
        ("user", None),
        ("ts", "invalid"),
    ],
)
def test_ignored_events(settings, field, value):
    queue = Queue()
    client = create_app(settings, queue).test_client()
    payload = message_payload()
    payload["event"][field] = value
    assert signed(client, settings, "/slack/events", payload).status_code == 200
    assert not queue.jobs


def test_workspace_isolation(settings):
    client = create_app(settings, Queue()).test_client()
    payload = message_payload()
    payload["team_id"] = "T999"
    assert signed(client, settings, "/slack/events", payload).status_code == 403


@pytest.mark.parametrize(
    "endpoint,command", [("/slack/commands", "/send-stats"), ("/send-stats", "/send-stats")]
)
def test_commands_from_other_channels_queue_report(settings, endpoint, command):
    queue = Queue()
    client = create_app(settings, queue).test_client()
    payload = {
        "team_id": "T123",
        "channel_id": "C999",
        "command": command,
        "trigger_id": "123.456.token",
        "text": "password",
        "response_url": "SECRET",
    }
    for _ in range(2):
        response = signed(client, settings, endpoint, payload, form=True)
        assert response.status_code == 200
        assert response.json["response_type"] == "ephemeral"
    assert len(queue.jobs) == 1
    assert "SECRET" not in str(queue.jobs)
    assert next(iter(queue.jobs.values()))[0] == "report"


def test_wordle_stats_command_is_not_supported(settings):
    queue = Queue()
    client = create_app(settings, queue).test_client()
    payload = {
        "team_id": "T123",
        "command": "/wordle-stats",
        "trigger_id": "123.456.token",
        "text": "password",
    }
    response = signed(client, settings, "/slack/commands", payload, form=True)
    assert response.status_code == 200
    assert response.json["text"] == "Usage: /send-stats password"
    assert not queue.jobs


@pytest.mark.parametrize("password", ["", "wrong", "password extra"])
def test_send_stats_rejects_invalid_password(settings, password):
    queue = Queue()
    client = create_app(settings, queue).test_client()
    payload = {
        "team_id": "T123",
        "command": "/send-stats",
        "trigger_id": "123.456.invalid",
        "text": password,
    }
    response = signed(client, settings, "/slack/commands", payload, form=True)
    assert response.status_code == 200
    assert response.json["text"] == "Usage: /send-stats password"
    assert not queue.jobs


@pytest.mark.parametrize(
    "error,status",
    [
        (OperationalError("private SQL", {}, Exception("SECRET")), 503),
        (RuntimeError("SECRET"), 500),
    ],
)
def test_queue_failure_is_not_acknowledged(settings, error, status):
    class BrokenQueue:
        def enqueue(self, *args):
            raise error

    client = create_app(settings, BrokenQueue()).test_client()
    result = signed(client, settings, "/slack/events", message_payload())
    assert result.status_code == status
    assert "SECRET" not in result.text


def test_health_without_slack_api_calls(settings):
    client = create_app(settings, Queue()).test_client()
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    assert client.get("/score").status_code == 404
    assert client.get("/msg").status_code == 404
    status = client.get("/health/status")
    assert status.status_code == 503
    assert status.json["backend"]["status"] == "up"
    assert status.json["database"]["status"] == "unknown"
    assert status.headers["Cache-Control"] == "no-store"
