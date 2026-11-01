"""Run only against an explicitly supplied, disposable database named *_test."""

import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from conftest import message_payload
from sqlalchemy import text
from sqlalchemy.engine import make_url
from test_http import signed

from wordle_bot.config import Settings
from wordle_bot.http import create_app
from wordle_bot.migrate import migrate
from wordle_bot.repositories.postgres import MAX_ATTEMPTS, PostgresRepository, create_db_engine
from wordle_bot.services import WordleService
from wordle_bot.worker import Worker

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test")
    assert (make_url(url).database or "").endswith("_test"), (
        "Refusing to change a non-test database"
    )
    settings = Settings(_env_file=None, database_url=url)
    migrate(settings)
    engine = create_db_engine(settings)
    yield settings, engine
    engine.dispose()


@pytest.fixture
def repository(database):
    settings, engine = database
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE wordle_bot.jobs, wordle_bot.participation, "
                "wordle_bot.worker_heartbeats RESTART IDENTITY"
            )
        )
    return PostgresRepository(engine)


def test_idempotent_migrations_preserve_records(database, repository):
    settings, engine = database
    repository.record("T1", "C1", "U1", date(2026, 10, 1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: migrate(settings), range(2)))
    assert repository.counts("T1", "C1", date(2026, 9, 28), date(2026, 10, 2))[0].days == 1
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM wordle_bot.alembic_version")).scalar()
            == "0004"
        )
        assert (
            connection.execute(
                text(
                    "SELECT bool_and(relrowsecurity) FROM pg_class "
                    "WHERE relnamespace = 'wordle_bot'::regnamespace AND relname IN "
                    "('participation','jobs','worker_heartbeats')"
                )
            ).scalar()
            is True
        )


def test_public_health_runs_select_one_and_does_not_write(database, repository, settings):
    from sqlalchemy import event

    configured = settings.model_copy(update={"database_url": database[0].database_url})
    client = create_app(configured).test_client()
    engine = client.application.extensions["wordle_repository"].engine
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        before = client.get("/health/status")
        assert before.status_code == 503
        assert before.json["database"]["status"] == "up"
        repository.heartbeat("health-test")
        after = client.get("/health/status")
        assert after.status_code == 200
        assert after.json["worker"]["status"] == "up"
        assert after.json["scheduler"]["status"] == "not_applicable"
        assert any(statement == "SELECT 1" for statement in statements)
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()


def test_platform_upgrade_from_legacy_preserves_participation(database, repository):
    from alembic import command
    from alembic.config import Config

    from wordle_bot.migrate import migration_directory

    settings, engine = database
    repository.record("T1", "C1", "U1", date(2026, 10, 1))
    config = Config()
    config.set_main_option("script_location", str(migration_directory()))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    migrate(settings)
    assert repository.counts("T1", "C1", date(2026, 9, 28), date(2026, 10, 2))[0].days == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT bool_and(relrowsecurity) FROM pg_class "
                    "WHERE relnamespace = 'wordle_bot'::regnamespace AND relkind = 'r' "
                    "AND relname != 'alembic_version'"
                )
            ).scalar()
            is True
        )
        assert (
            connection.execute(
                text("SELECT source_at FROM wordle_bot.participation WHERE user_id = 'U1'")
            ).scalar()
            is None
        )


def test_concurrent_participation_and_week_isolation(repository):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: repository.record("T1", "C1", "U1", date(2026, 10, 1)), range(20)))
    for day in (date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 5)):
        repository.record("T1", "C1", "U1", day)
    repository.record("T2", "C1", "U1", date(2026, 10, 1))
    repository.record("T1", "C2", "U1", date(2026, 10, 1))
    assert repository.counts("T1", "C1", date(2026, 9, 28), date(2026, 10, 4))[0].days == 2
    assert repository.counts("T1", "C1", date(2026, 10, 5), date(2026, 10, 9))[0].days == 1


def test_concurrent_queue_claims(repository):
    with ThreadPoolExecutor(max_workers=8) as pool:
        inserted = list(pool.map(lambda _: repository.enqueue("same", "message", {}), range(20)))
    assert sum(inserted) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: repository.claim(120), range(8)))
    claimed = [job for job in jobs if job]
    assert len(claimed) == 1
    repository.complete(claimed[0])
    assert repository.jobs("done")[0]["attempts"] == 1
    assert repository.claim(120) is None


def test_lease_recovery_rejects_stale_owner(repository, database):
    repository.enqueue("lease", "message", {})
    original = repository.claim(120)
    with database[1].begin() as connection:
        connection.execute(
            text("UPDATE wordle_bot.jobs SET lease_until = :past"),
            {"past": datetime.now(UTC) - timedelta(seconds=1)},
        )
    recovered = repository.claim(120)
    assert recovered.id == original.id
    assert recovered.lease_token != original.lease_token
    assert recovered.attempts == 2
    assert repository.renew(original, 120) is False
    repository.complete(original)
    assert repository.jobs("running")
    repository.complete(recovered)
    assert repository.jobs("done")


def test_exhausted_retries_and_explicit_retry(repository):
    repository.enqueue("retry", "message", {})
    for _ in range(MAX_ATTEMPTS):
        job = repository.claim(120)
        assert job is not None
        repository.fail(job, "network_error", 0)
    assert repository.claim(120) is None
    assert repository.jobs("failed")[0]["attempts"] == MAX_ATTEMPTS
    assert repository.retry(job.id)
    assert repository.claim(120).attempts == 1


def test_final_expired_lease_becomes_failed(repository, database):
    repository.enqueue("final-lease", "message", {})
    repository.claim(120)
    with database[1].begin() as connection:
        connection.execute(
            text("UPDATE wordle_bot.jobs SET attempts = 5, lease_until = :past"),
            {"past": datetime.now(UTC) - timedelta(seconds=1)},
        )
    assert repository.claim(120) is None
    assert repository.jobs("failed")[0]["last_error"] == "lease_expired"


def test_worker_health(repository, database):
    assert not repository.healthy(90)
    repository.heartbeat("worker")
    assert repository.healthy(90, "worker")
    assert not repository.healthy(90, "other")
    with database[1].begin() as connection:
        connection.execute(
            text("UPDATE wordle_bot.worker_heartbeats SET updated_at = :past"),
            {"past": datetime.now(UTC) - timedelta(seconds=100)},
        )
    assert not repository.healthy(90)


@pytest.mark.e2e
def test_http_worker_roundtrip(repository, database, settings):
    configured = settings.model_copy(update={"database_url": database[0].database_url})
    app = create_app(configured)
    client = app.test_client()
    payload = message_payload()
    payload["event"]["ts"] = str(datetime(2026, 10, 1, 12, tzinfo=UTC).timestamp())
    try:
        assert client.get("/health/ready").status_code == 503
        repository.heartbeat("roundtrip-worker")
        assert client.get("/health/ready").status_code == 200
        for _ in range(2):
            assert signed(client, configured, "/slack/events", payload).status_code == 200
        slack = Mock()
        worker = Worker(configured, repository, WordleService(configured, repository, slack))
        job = repository.claim(120)
        assert job is not None
        worker.process(job)
        slack.react.assert_called_once_with("C123", payload["event"]["ts"], "fire")
        assert repository.counts("T123", "C123", date(2026, 9, 28), date(2026, 10, 2))[0].days == 1
        assert repository.claim(120) is None
    finally:
        app.extensions["wordle_repository"].engine.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("platform_mode", [False, True])
def test_combined_startup_and_graceful_shutdown(
    repository, database, settings, tmp_path, platform_mode
):
    from cryptography.fernet import Fernet

    (tmp_path / "index.html").write_text("<html>Wordle admin</html>")
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    environment = {
        **os.environ,
        "DB_BACKEND": "local",
        "ADMIN_PANEL_ENABLED": str(platform_mode).lower(),
        "FRONTEND_DIR": str(tmp_path),
        "JWT_SECRET_KEY": "s" * 64,
        "CREDENTIAL_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
        "PERSIST_LOGS": "false",
        "DATABASE_URL": database[0].database_url.get_secret_value(),
        "SLACK_BOT_TOKEN": settings.slack_bot_token.get_secret_value(),
        "SLACK_SIGNING_SECRET": settings.slack_signing_secret.get_secret_value(),
        "SLACK_TEAM_ID": settings.slack_team_id,
        "SLACK_CHANNEL_ID": settings.slack_channel_id,
        "SEND_STATS_PASSWORD": settings.send_stats_password.get_secret_value(),
        "PORT": str(port),
        "AUTO_MIGRATE": "true",
        "WORKER_POLL_SECONDS": "0.2",
        "MIGRATION_DATABASE_URL": database[0].database_url.get_secret_value(),
    }
    logfile = tmp_path / "combined.log"
    with logfile.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "wordle_bot.cli", "combined"],
            env=environment,
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, logfile.read_text()
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=1) as response:
                        assert response.status == 200
                        break
                except URLError:
                    time.sleep(0.1)
            else:
                pytest.fail("Combined runtime did not become ready: " + logfile.read_text())
            if platform_mode:
                with urlopen(f"http://127.0.0.1:{port}/workspaces", timeout=2) as response:
                    assert b"Wordle admin" in response.read()
                assert "Scheduler started" in logfile.read_text()
        finally:
            process.terminate()
            process.wait(timeout=15)
    assert process.returncode == 0, logfile.read_text()
