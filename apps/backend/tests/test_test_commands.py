"""Test command guards must reject unsafe URLs without leaking their contents."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url-sensitive-value",
        "sqlite:///wordle_test",
        "postgresql://user:sensitive-password@localhost/production",
        "postgresql://user:sensitive-password@localhost:invalid/wordle_test",
    ],
)
def test_database_preflight_rejects_unsafe_configuration(url):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_test_database.py"), "TEST_DATABASE_URL"],
        env={**os.environ, "TEST_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "TEST_DATABASE_URL" in result.stderr
    assert "sensitive" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.fixture
def compose_runner():
    path = Path(__file__).resolve().parents[3] / "scripts/run_tests.py"
    spec = importlib.util.spec_from_file_location("compose_test_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name,application",
    [
        ("wordle", "wordle"),
        ("production", "wordle"),
        ("wordle_test", "wordle_test"),
        ("x_test; DROP DATABASE wordle", "wordle"),
    ],
)
def test_compose_runner_rejects_application_and_unsafe_database_names(
    compose_runner, name, application
):
    with pytest.raises(ValueError):
        compose_runner.database_name(name, application)


def test_compose_runner_reuses_db_and_creates_only_separate_test_database(
    compose_runner, monkeypatch
):
    monkeypatch.setenv("TEST_DB_NAME", "wordle_test")
    config = {
        "services": {
            "db": {
                "image": "postgres:17.11-bookworm",
                "environment": {
                    "POSTGRES_USER": "wordle",
                    "POSTGRES_PASSWORD": "test-only:@password",
                    "POSTGRES_DB": "wordle",
                },
            }
        }
    }
    connection = Mock()
    connection.info.server_version = 170011
    connection.execute.return_value.fetchone.return_value = None
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    connect = Mock(return_value=context)
    monkeypatch.setattr(compose_runner.psycopg, "connect", connect)
    database = compose_runner.ComposeDatabase()
    responses = {
        ("config", "--format", "json", "db"): json.dumps(config),
        ("ps", "--status", "running", "-q", "db"): "existing-container",
        ("port", "db", "5432"): "127.0.0.1:15432",
    }
    database.run = Mock(side_effect=lambda *args: responses[args])
    url = database.prepare(create=True)
    assert url.database == "wordle_test"
    assert url.port == 15432
    assert url.password == "test-only:@password"
    assert not any(call.args[0] == "up" for call in database.run.call_args_list)
    queries = [call.args[0] for call in connection.execute.call_args_list]
    create = [query.as_string() for query in queries if not isinstance(query, str)]
    assert create == ['CREATE DATABASE "wordle_test"']


def test_compose_check_does_not_start_a_stopped_database(compose_runner, monkeypatch):
    monkeypatch.setenv("TEST_DB_NAME", "wordle_test")
    database = compose_runner.ComposeDatabase()
    database.run = Mock(
        side_effect=[
            json.dumps(
                {
                    "services": {
                        "db": {
                            "image": "postgres:17.11-bookworm",
                            "environment": {"POSTGRES_DB": "wordle"},
                        }
                    }
                }
            ),
            "",
        ]
    )
    with pytest.raises(ValueError, match="stopped"):
        database.prepare(create=False)
    assert database.run.call_count == 2
