from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from wordle_bot.health import health_status


def test_status_queries_database_and_aggregates_heartbeats(settings):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    now = datetime.now(UTC)
    connection.execute.return_value.scalar_one.return_value = 1
    connection.execute.return_value.one.return_value = (now, now)
    data, code = health_status(engine, settings.model_copy(update={"admin_panel_enabled": True}))
    assert code == 200
    assert data["status"] == "operational"
    assert data["database"]["status"] == "up"
    assert str(connection.execute.call_args_list[0].args[0]) == "SELECT 1"
    assert connection.execute.call_count == 2


def test_database_failure_is_safe_and_independent(settings):
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("secret-url", {}, Exception("password"))
    data, code = health_status(engine, settings)
    assert code == 503
    assert data["backend"]["status"] == "up"
    assert data["database"]["status"] == "down"
    assert data["worker"]["status"] == "unknown"
    assert "secret" not in str(data) and "password" not in str(data)


def test_stale_worker_and_disabled_scheduler(settings):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = 1
    connection.execute.return_value.one.return_value = (
        datetime.now(UTC) - timedelta(seconds=120),
        None,
    )
    data, code = health_status(engine, settings)
    assert code == 503
    assert data["database"]["status"] == "up"
    assert data["worker"]["status"] == "down"
    assert data["scheduler"]["status"] == "not_applicable"


def test_missing_database_reports_unknown(settings):
    data, code = health_status(None, settings)
    assert code == 503
    assert data["database"]["status"] == "unknown"
