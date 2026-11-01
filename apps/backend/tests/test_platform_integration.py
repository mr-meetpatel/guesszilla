"""Platform acceptance tests against disposable PostgreSQL, never SQLite."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.http import create_app
from wordle_bot.migrate import migrate
from wordle_bot.platform.auth import bootstrap_admin, digest, passwords
from wordle_bot.platform.models import (
    Admin,
    AdminSession,
    Channel,
    Delivery,
    OAuthState,
    Occurrence,
    Participant,
    Report,
    Schedule,
    Workspace,
)
from wordle_bot.platform.reports import OutboxDispatcher, ReportService
from wordle_bot.platform.scheduler import Scheduler
from wordle_bot.repositories.postgres import create_db_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def platform():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required")
    assert (make_url(url).database or "").endswith("_test")
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(url),
        admin_panel_enabled=True,
        jwt_secret_key=SecretStr("s" * 64),
        credential_encryption_keys=SecretStr(Fernet.generate_key().decode()),
        cookie_secure=False,
        persist_logs=False,
    )
    migrate(settings)
    engine = create_db_engine(settings)
    from wordle_bot.repositories.models import Base

    with engine.begin() as conn:
        names = ",".join(f"wordle_bot.{t.name}" for t in Base.metadata.sorted_tables)
        conn.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
    bootstrap_admin(engine, "admin", "a-test-password-123")
    yield settings, engine
    engine.dispose()


def login(client):
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "a-test-password-123"}
    )
    assert response.status_code == 200, response.json
    return {"X-CSRF-TOKEN": client.get_cookie("csrf_access_token").value}


def test_auth_csrf_revocation_and_protected_routes(platform):
    app = create_app(platform[0])
    client = app.test_client()
    assert client.get("/api/v1/overview").status_code == 401
    headers = login(client)
    assert client.get("/api/v1/overview").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 401
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/overview").status_code == 401


def test_refresh_rotation_replay_revokes_family(platform):
    client = create_app(platform[0]).test_client()
    login(client)
    refresh = client.get_cookie("refresh_token_cookie", path="/api/v1/auth/").value
    csrf = client.get_cookie("csrf_refresh_token").value
    response = client.post("/api/v1/auth/refresh", headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 200
    client.set_cookie("refresh_token_cookie", refresh, path="/api/v1/auth/")
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF-TOKEN": csrf}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def seed_channel(settings, engine):
    crypto = Fernet(settings.credential_encryption_keys.get_secret_value().encode())
    with Session(engine) as db, db.begin():
        db.add(
            Workspace(id="T1", name="Test", encrypted_token=crypto.encrypt(b"xoxb-test").decode())
        )
        db.flush()
        channel = Channel(
            workspace_id="T1",
            slack_id="C1",
            name="wordle",
            rules={
                "timezone": "Asia/Kolkata",
                "weekdays": [0, 1, 2, 3, 4],
                "threshold": 5,
            },
            password_hash=passwords.hash("channel-password"),
        )
        db.add(channel)
        db.flush()
        return channel.id


@pytest.mark.e2e
def test_slack_ingress_manual_schedule_outbox_and_admin_receipts(platform, monkeypatch):
    """Exercise the managed-channel workflow; outbound Slack is the only mocked service."""
    from conftest import wordle_text
    from test_http import signed

    from wordle_bot.platform.oauth import SlackInstallations
    from wordle_bot.repositories.postgres import PostgresRepository
    from wordle_bot.services import WordleService
    from wordle_bot.worker import Worker

    settings, engine = platform
    settings = settings.model_copy(update={"slack_signing_secret": SecretStr("e2e-signing-secret")})
    channel_id = seed_channel(settings, engine)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    with Session(engine) as db, db.begin():
        db.add_all(
            [
                Participant(channel_id=channel_id, user_id="U1", name="Amy"),
                Participant(channel_id=channel_id, user_id="U2", name="Bob"),
            ]
        )
    slack = Mock()
    slack.chat_postMessage.return_value = {"ok": True, "ts": "123.456"}
    monkeypatch.setattr(SlackInstallations, "client", lambda *args: slack)
    repository = PostgresRepository(engine)
    worker = Worker(settings, repository, WordleService(settings, repository, slack))
    client = create_app(settings).test_client()
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "EvE2E",
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "text": wordle_text("3"),
            "ts": str(datetime(monday.year, monday.month, monday.day, 12, tzinfo=UTC).timestamp()),
        },
    }
    for _ in range(2):
        assert signed(client, settings, "/slack/events", payload).status_code == 200
    job = repository.claim(120)
    assert job is not None and job.kind == "message"
    worker.process(job)
    assert repository.claim(120) is None
    slack.reactions_add.assert_called_once()

    command = {
        "team_id": "T1",
        "channel_id": "C1",
        "command": "/send-stats",
        "text": "channel-password",
        "trigger_id": "e2e-trigger",
    }
    for _ in range(2):
        assert signed(client, settings, "/slack/commands", command, form=True).status_code == 200
    job = repository.claim(120)
    assert job is not None and job.kind == "report"
    worker.process(job)
    assert repository.claim(120) is None
    due = datetime(friday.year, friday.month, friday.day, 11, 30, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        db.add(
            Schedule(
                channel_id=channel_id,
                name="Friday",
                frequency="weekly",
                weekdays=[4],
                wall_time="17:00",
                timezone="Asia/Kolkata",
                window="current_week",
                rolling_days=7,
                paused=False,
                next_at=due,
            )
        )
    assert Scheduler(settings, engine).tick(due)
    dispatcher = OutboxDispatcher(settings, engine, client_factory=lambda token: slack)
    assert dispatcher.tick()
    assert not dispatcher.tick()
    slack.chat_postMessage.assert_called_once()

    login(client)
    reports = client.get(f"/api/v1/reports?channel_id={channel_id}").json["data"]
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "delivered"
    assert [(p["name"], p["days"]) for p in report["snapshot"]["players"]] == [
        ("Amy", 1),
        ("Bob", 0),
    ]
    receipts = client.get(f"/api/v1/reports/{report['id']}/deliveries").json["data"]
    assert len(receipts) == 1
    assert receipts[0]["slack_ts"] == "123.456"
    with Session(engine) as db:
        assert db.scalar(select(Occurrence)).report_id == report["id"]


def test_concurrent_manual_and_schedule_create_one_report(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    service = ReportService(settings, engine)

    def create(number):
        return service.request_report(
            channel_id, date(2026, 10, 26), date(2026, 10, 30), f"intent-{number}", "test"
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(create, range(8)))
    assert len({report["id"] for report in results}) == 1
    with Session(engine) as db:
        assert len(list(db.scalars(select(Report)))) == 1
        assert len(list(db.scalars(select(Delivery)))) == 1


def test_outbox_timeout_is_uncertain_and_never_reposted(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    slack = Mock()
    slack.chat_postMessage.side_effect = TimeoutError
    slack.conversations_history.return_value = {"messages": [], "has_more": False}
    dispatcher = OutboxDispatcher(settings, engine, client_factory=lambda token: slack)
    dispatcher.tick()
    dispatcher.tick()
    assert slack.chat_postMessage.call_count == 1
    with Session(engine) as db:
        assert db.scalar(select(Delivery)).status == "uncertain"


def test_crash_after_acceptance_reconciles_without_post(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    with Session(engine) as db, db.begin():
        delivery = db.scalar(select(Delivery))
        delivery.status = "sending"
        delivery.sending_at = datetime.now(UTC) - timedelta(minutes=3)
        delivery.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        delivery_id = delivery.id
    slack = Mock()
    slack.conversations_history.return_value = {
        "messages": [
            {
                "ts": "123.456",
                "bot_id": "B1",
                "metadata": {
                    "event_type": "wordle_report",
                    "event_payload": {"delivery_id": delivery_id},
                },
            }
        ],
        "has_more": False,
    }
    with Session(engine) as db, db.begin():
        db.get(Workspace, "T1").bot_id = "B1"
    dispatcher = OutboxDispatcher(settings, engine, client_factory=lambda token: slack)
    dispatcher.tick()
    slack.chat_postMessage.assert_not_called()
    with Session(engine) as db:
        assert db.scalar(select(Delivery)).status == "sent"
        assert db.scalar(select(Report)).status == "delivered"


def test_scheduler_race_and_manual_send_share_one_report(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        db.add(
            Schedule(
                channel_id=channel_id,
                name="Friday",
                frequency="weekly",
                weekdays=[4],
                wall_time="17:00",
                timezone="Asia/Kolkata",
                window="current_week",
                rolling_days=7,
                paused=False,
                next_at=now - timedelta(minutes=30),
            )
        )

    def tick(index):
        if index % 2:
            Scheduler(settings, engine).tick(now)
        else:
            ReportService(settings, engine).request_report(
                channel_id, date(2026, 10, 26), date(2026, 10, 30), f"manual-{index}", "test"
            )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(tick, range(12)))
    with Session(engine) as db:
        assert len(list(db.scalars(select(Occurrence)))) == 1
        assert len(list(db.scalars(select(Report)))) == 1
        assert len(list(db.scalars(select(Delivery)))) == 1


def test_missed_runs_only_latest_within_24h(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        db.add(
            Schedule(
                channel_id=channel_id,
                name="Daily",
                frequency="daily",
                weekdays=[4],
                wall_time="17:00",
                timezone="Asia/Kolkata",
                window="current_week",
                rolling_days=7,
                paused=False,
                next_at=now - timedelta(days=10, minutes=30),
            )
        )
    Scheduler(settings, engine).tick(now)
    with Session(engine) as db:
        occurrences = list(db.scalars(select(Occurrence)))
        assert sum(o.status == "missed" for o in occurrences) == 10
        assert sum(o.status == "queued" for o in occurrences) == 1
        assert len(list(db.scalars(select(Report)))) == 1


def test_no_backlog_post_after_older_weekly_miss(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    with Session(engine) as db, db.begin():
        db.add(
            Schedule(
                channel_id=channel_id,
                name="Weekly",
                frequency="weekly",
                weekdays=[4],
                wall_time="17:00",
                timezone="Asia/Kolkata",
                window="current_week",
                rolling_days=7,
                paused=False,
                next_at=datetime(2026, 10, 23, 11, 30, tzinfo=UTC),
            )
        )
    Scheduler(settings, engine).tick(datetime(2026, 11, 2, tzinfo=UTC))
    with Session(engine) as db:
        assert len(list(db.scalars(select(Report)))) == 0
        assert all(o.status == "missed" for o in db.scalars(select(Occurrence)))


def test_snapshot_survives_rule_and_roster_changes(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    with Session(engine) as db, db.begin():
        db.add(Participant(channel_id=channel_id, user_id="U1", name="Amy"))
    service = ReportService(settings, engine)
    original = service.request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    with Session(engine) as db, db.begin():
        db.get(Participant, (channel_id, "U1")).name = "Zed"
        db.get(Channel, channel_id).rules = {"threshold": 3}
    repeated = service.request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "two", "test"
    )
    assert repeated["id"] == original["id"]
    assert repeated["snapshot"]["players"][0]["name"] == "Amy"
    assert repeated["snapshot"]["threshold"] == 5


def test_all_admin_routes_require_jwt(platform):
    app = create_app(platform[0])
    client = app.test_client()
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api/v1/") and not rule.rule.startswith("/api/v1/auth/"):
            path = rule.rule.replace("<workspace_id>", "T1").replace("<channel_id>", "C1")
            for name in (
                "report_id",
                "delivery_id",
                "schedule_id",
                "admin_id",
                "session_id",
                "user_id",
            ):
                path = path.replace(f"<{name}>", "missing")
            path = path.replace("<int:job_id>", "1")
            method = (
                "GET"
                if "GET" in rule.methods
                else "PATCH"
                if "PATCH" in rule.methods
                else "DELETE"
                if "DELETE" in rule.methods
                else "POST"
            )
            assert client.open(path, method=method, json={}).status_code == 401, path


def test_oauth_state_replay_and_revoked_session(platform):
    settings, engine = platform
    client = create_app(settings).test_client()
    login(client)
    with Session(engine) as db, db.begin():
        session = db.scalar(select(AdminSession))
        session.revoked = True
        db.add(
            OAuthState(
                digest=digest("known-state"),
                session_id=session.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
    assert client.get("/slack/oauth/callback?state=known-state&code=fake").status_code == 401
    assert client.get("/slack/oauth/callback?state=unrecognized&code=fake").status_code == 400


def test_oauth_connect_queues_discovery_and_selects_workspace(platform, monkeypatch):
    from wordle_bot.repositories.models import Job

    settings, engine = platform
    client = create_app(settings).test_client()
    login(client)
    with Session(engine) as db, db.begin():
        session = db.scalar(select(AdminSession))
        db.add(
            OAuthState(
                digest=digest("connect-state"),
                session_id=session.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
    slack = Mock()
    slack.oauth_v2_access.return_value = {
        "team": {"id": "TNEW", "name": "New workspace"},
        "access_token": "xoxb-new",
        "bot_user_id": "BNEW",
    }
    monkeypatch.setattr("wordle_bot.platform.oauth.WebClient", lambda **kwargs: slack)
    response = client.get("/slack/oauth/callback?state=connect-state&code=fake")
    assert response.status_code == 302
    assert response.location == "/workspaces?oauth=connected&workspace_id=TNEW"
    with Session(engine) as db:
        assert db.get(Workspace, "TNEW") is not None
        job = db.scalar(select(Job).where(Job.kind == "workspace_sync"))
        assert job.payload == {"workspace_id": "TNEW"}
    assert client.get("/slack/oauth/callback?state=connect-state&code=fake").status_code == 400
    with Session(engine) as db:
        assert len(list(db.scalars(select(Job)))) == 1


@pytest.mark.parametrize("existing", [False, True])
def test_wordle_submission_registers_missing_participant_once(platform, monkeypatch, existing):
    from conftest import wordle_text

    from wordle_bot.platform.ingress import PlatformTasks
    from wordle_bot.platform.oauth import SlackInstallations
    from wordle_bot.repositories.models import Participation

    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    if existing:
        with Session(engine) as db, db.begin():
            db.add(
                Participant(channel_id=channel_id, user_id="U1", name="Existing", included=False)
            )
    slack = Mock()
    slack.users_info.return_value = {"user": {"id": "U1", "profile": {"display_name": "Amy"}}}
    monkeypatch.setattr(SlackInstallations, "client", lambda *args: slack)
    tasks = PlatformTasks(settings, engine)
    payload = {"channel_id": channel_id, "user": "U1", "ts": "1790856000", "text": wordle_text()}
    tasks.record_message({**payload, "text": "Hello"})
    slack.users_info.assert_not_called()
    tasks.record_message(payload)
    tasks.record_message(payload)
    with Session(engine) as db:
        roster = list(db.scalars(select(Participant)))
        assert len(roster) == 1
        assert roster[0].name == ("Existing" if existing else "Amy")
        assert roster[0].included is (not existing)
        assert len(list(db.scalars(select(Participation)))) == 1
    if existing:
        slack.users_info.assert_not_called()
    else:
        slack.users_info.assert_called_once_with(user="U1")


def test_concurrent_first_wordle_submissions_register_one_player(platform, monkeypatch):
    from threading import Barrier

    from conftest import wordle_text

    from wordle_bot.platform.ingress import PlatformTasks
    from wordle_bot.platform.oauth import SlackInstallations
    from wordle_bot.repositories.models import Participation

    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    barrier = Barrier(2)
    slack = Mock()

    def profile(**kwargs):
        barrier.wait(timeout=5)
        return {"user": {"profile": {"display_name": "Amy"}}}

    slack.users_info.side_effect = profile
    monkeypatch.setattr(SlackInstallations, "client", lambda *args: slack)
    payload = {"channel_id": channel_id, "user": "U1", "ts": "1790856000", "text": wordle_text()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(PlatformTasks(settings, engine).record_message, payload) for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=10)
    with Session(engine) as db:
        assert len(list(db.scalars(select(Participant)))) == 1
        assert len(list(db.scalars(select(Participation)))) == 1


def test_last_admin_and_stale_settings_are_protected(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    client = create_app(settings).test_client()
    headers = login(client)
    with Session(engine) as db:
        admin_id = db.scalar(select(Admin.id))
    assert (
        client.patch(
            f"/api/v1/admins/{admin_id}", json={"active": False}, headers=headers
        ).status_code
        == 409
    )
    response = client.patch(
        f"/api/v1/channels/{channel_id}",
        json={"version": 1, "rules": {"threshold": 3}},
        headers=headers,
    )
    assert response.status_code == 200
    assert (
        client.patch(
            f"/api/v1/channels/{channel_id}",
            json={"version": 1, "rules": {"threshold": 1}},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/channels/{channel_id}/participants/OTHER",
            json={"included": False},
            headers=headers,
        ).status_code
        == 404
    )


def test_two_outbox_workers_send_once(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    slack = Mock()
    slack.chat_postMessage.return_value = {"ok": True, "ts": "123.45"}

    def tick(_):
        return OutboxDispatcher(settings, engine, client_factory=lambda token: slack).tick()

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(tick, range(8)))
    assert slack.chat_postMessage.call_count == 1


def test_channel_discovery_preserves_settings_and_refreshes_bot_identity(platform):
    from wordle_bot.platform.oauth import SlackInstallations

    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    slack = Mock()
    slack.auth_test.return_value = {"ok": True, "team_id": "T1", "user_id": "UBOT"}
    slack.conversations_list.return_value = {
        "channels": [{"id": "C1", "name": "renamed"}, {"id": "C2", "name": "new"}]
    }
    installation = SlackInstallations(settings, engine)
    installation.client = Mock(return_value=slack)
    installation.discover("T1")
    with Session(engine) as db:
        assert db.get(Workspace, "T1").bot_id == "UBOT"
        channel = db.get(Channel, channel_id)
        assert channel.active is True
        assert channel.name == "renamed"
        assert channel.rules["threshold"] == 5
        assert db.scalar(select(Channel).where(Channel.slack_id == "C2")).active is False


def test_roster_sync_preserves_exclusions_and_filters_non_humans(platform):
    from wordle_bot.platform.oauth import SlackInstallations

    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    with Session(engine) as db, db.begin():
        db.add(Participant(channel_id=channel_id, user_id="U1", name="Old", included=False))
        db.add(Participant(channel_id=channel_id, user_id="LEFT", name="Former"))
    slack = Mock()
    slack.conversations_members.return_value = {"members": ["U1", "U2", "BOT", "APP", "DELETED"]}
    slack.users_list.return_value = {
        "members": [
            {"id": "U1", "profile": {"display_name": "Renamed"}},
            {"id": "U2", "profile": {"real_name": "New player"}},
            {"id": "BOT", "is_bot": True},
            {"id": "APP", "is_app_user": True},
            {"id": "DELETED", "deleted": True},
        ]
    }
    installation = SlackInstallations(settings, engine)
    installation.client = Mock(return_value=slack)
    installation.sync_roster(channel_id)
    with Session(engine) as db:
        roster = {p.user_id: p for p in db.scalars(select(Participant))}
        assert set(roster) == {"U1", "U2", "LEFT"}
        assert roster["U1"].included is False
        assert roster["U1"].name == "Renamed"
        assert roster["U2"].included is True
        assert roster["LEFT"].active is False


def test_roster_sync_stops_when_lease_is_lost(platform):
    from wordle_bot.platform.oauth import SlackInstallations

    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    installation = SlackInstallations(settings, engine, progress=lambda: False)
    slack = Mock()
    installation.client = Mock(return_value=slack)
    with pytest.raises(ValueError, match="lease was lost"):
        installation.sync_roster(channel_id)
    slack.conversations_members.assert_not_called()


def test_uncertain_delivery_with_unavailable_credential_remains_blocked(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    report = ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    with Session(engine) as db, db.begin():
        delivery = db.scalar(select(Delivery))
        delivery.status = "uncertain"
        delivery.sending_at = datetime.now(UTC)
        db.get(Workspace, "T1").encrypted_token = "broken-credential"
    slack = Mock()
    OutboxDispatcher(settings, engine, client_factory=lambda token: slack).tick()
    slack.chat_postMessage.assert_not_called()
    with Session(engine) as db:
        assert db.scalar(select(Delivery)).status == "uncertain"
        assert db.get(Report, report["id"]).status == "uncertain"


def test_schedule_activation_rejects_impossible_royalty_window(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    client = create_app(settings).test_client()
    headers = login(client)
    payload = {"window": "rolling", "rolling_days": 1, "paused": True}
    result = client.post(f"/api/v1/channels/{channel_id}/schedules", json=payload, headers=headers)
    assert result.status_code == 200
    schedule = result.json["data"]
    assert (
        client.patch(
            f"/api/v1/schedules/{schedule['id']}",
            json={"version": schedule["version"], "paused": False},
            headers=headers,
        ).status_code
        == 422
    )


def test_password_mutations_reject_non_string_values(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    client = create_app(settings).test_client()
    headers = login(client)
    malformed = {"unexpected": "long-enough-for-length-check"}
    assert (
        client.patch(
            f"/api/v1/channels/{channel_id}",
            json={"version": 1, "password": malformed},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/password",
            json={"current_password": "a-test-password-123", "password": malformed},
            headers=headers,
        ).status_code
        == 422
    )


def test_login_throttle_and_expired_session(platform):
    client = create_app(platform[0]).test_client()
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "incorrect"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "a-test-password-123"}
        ).status_code
        == 429
    )


def test_expired_access_refresh_and_wrong_token_type(platform):
    from flask_jwt_extended import create_access_token

    settings, engine = platform
    app = create_app(settings)
    client = app.test_client()
    login(client)
    with Session(engine) as db:
        session = db.scalar(select(AdminSession))
        sid, admin_id = session.id, session.admin_id
    with app.app_context():
        expired = create_access_token(
            identity=admin_id, additional_claims={"sid": sid}, expires_delta=timedelta(seconds=-1)
        )
    client.set_cookie("access_token_cookie", expired, path="/api/")
    assert client.get("/api/v1/overview").status_code == 401
    csrf = client.get_cookie("csrf_refresh_token").value
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF-TOKEN": csrf}).status_code == 200
    assert client.get("/api/v1/overview").status_code == 200
    refresh = client.get_cookie("refresh_token_cookie", path="/api/v1/auth/").value
    client.set_cookie("access_token_cookie", refresh, path="/api/")
    assert client.get("/api/v1/overview").status_code == 422
    with Session(engine) as db, db.begin():
        db.get(AdminSession, sid).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    csrf = client.get_cookie("csrf_refresh_token").value
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF-TOKEN": csrf}).status_code == 401


def test_delivered_receipt_cannot_be_downgraded_by_stale_sender(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    slack = Mock()
    slack.chat_postMessage.return_value = {"ok": True, "ts": "123.45"}
    dispatcher = OutboxDispatcher(settings, engine, client_factory=lambda token: slack)
    dispatcher.tick()
    with Session(engine) as db:
        delivery = db.scalar(select(Delivery))
        delivery_id, fence = delivery.id, delivery.fence
    dispatcher._finish(delivery_id, fence, "uncertain", error="stale")
    with Session(engine) as db:
        assert db.get(Delivery, delivery_id).status == "sent"
        assert db.get(Delivery, delivery_id).slack_ts == "123.45"


def test_disconnect_cancels_unsent_work_but_preserves_history(platform):
    settings, engine = platform
    channel_id = seed_channel(settings, engine)
    report = ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    client = create_app(settings).test_client()
    headers = login(client)
    assert client.delete("/api/v1/workspaces/T1", headers=headers).status_code == 200
    assert (
        client.patch("/api/v1/workspaces/T1", json={"active": True}, headers=headers).status_code
        == 409
    )
    with Session(engine) as db:
        assert db.get(Workspace, "T1").encrypted_token == ""
        assert db.get(Channel, channel_id).active is False
        assert db.get(Report, report["id"]).status == "cancelled"
        assert db.scalar(select(Delivery)).status == "cancelled"


def test_application_log_sink_redacts_and_retention_preserves_receipts(platform):
    import logging
    import time

    from wordle_bot.platform.api import prune
    from wordle_bot.platform.models import Event
    from wordle_bot.platform.observability import EventLogHandler

    settings, engine = platform
    handler = EventLogHandler(engine, ["test-only-sensitive"])
    handler.emit(
        logging.LogRecord(
            "wordle_bot.test",
            logging.ERROR,
            "test",
            1,
            "test-only-sensitive xoxb-fake-token postgresql://user:secret@host/db",
            (),
            None,
        )
    )
    try:
        deadline = time.monotonic() + 3
        records = []
        while time.monotonic() < deadline:
            with Session(engine) as db:
                records = list(db.scalars(select(Event).where(Event.category == "log")))
            if records:
                break
            time.sleep(0.01)
        assert records
        assert "sensitive" not in records[0].action
        assert "xoxb" not in records[0].action
        assert "secret" not in records[0].action
    finally:
        handler.shutdown()
    channel_id = seed_channel(settings, engine)
    report = ReportService(settings, engine).request_report(
        channel_id, date(2026, 10, 26), date(2026, 10, 30), "one", "test"
    )
    with Session(engine) as db, db.begin():
        db.add(
            Event(
                category="log", action="expired", created_at=datetime.now(UTC) - timedelta(days=15)
            )
        )
    prune(engine)
    with Session(engine) as db:
        assert db.scalar(select(Event).where(Event.action == "expired")) is None
        assert db.get(Report, report["id"]) is not None
        assert db.scalar(select(Delivery)) is not None
