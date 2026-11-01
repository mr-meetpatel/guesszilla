"""Release gating and error handling without contacting Render."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest


@pytest.fixture
def deploy():
    path = Path(__file__).resolve().parents[3] / "scripts/deploy_render.py"
    spec = importlib.util.spec_from_file_location("render_deploy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environment():
    return {
        "RENDER_API_KEY": "secret-test-key",
        "RENDER_COMMIT_SHA": "a" * 40,
        **{
            f"RENDER_{name}_SERVICE_ID": f"srv-{name.lower()}"
            for name in ("API", "WORKER", "SCHEDULER", "ADMIN")
        },
    }


def test_missing_configuration_fails_before_any_deploy(deploy, environment, monkeypatch, capsys):
    monkeypatch.setattr(deploy.os, "environ", {})
    client = Mock()
    monkeypatch.setattr(deploy, "RenderClient", client)
    assert deploy.main() == 1
    client.assert_not_called()
    assert "RENDER_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["", "main", "abc123", "a" * 40 + "\ninvalid"])
def test_requires_full_commit_sha(deploy, environment, value):
    environment["RENDER_COMMIT_SHA"] = value
    with pytest.raises(deploy.DeploymentError):
        deploy.configuration(environment)


def test_duplicate_service_rejected(deploy, environment):
    environment["RENDER_WORKER_SERVICE_ID"] = environment["RENDER_API_SERVICE_ID"]
    with pytest.raises(deploy.DeploymentError, match="different service"):
        deploy.configuration(environment)


def test_failed_api_deploy_stops_release(deploy, environment, monkeypatch):
    monkeypatch.setattr(deploy.os, "environ", environment)
    client = Mock()
    client.deploy.side_effect = deploy.DeploymentError("failed")
    monkeypatch.setattr(deploy, "RenderClient", Mock(return_value=client))
    assert deploy.main() == 1
    client.deploy.assert_called_once_with("API", "srv-api", "a" * 40)


def test_pins_commit_and_waits_for_live(deploy, monkeypatch):
    client = deploy.RenderClient("secret-test-key")
    client.request = Mock(
        side_effect=[
            {"id": "dep-test"},
            {"status": "build_in_progress"},
            {"status": "live", "commit": {"id": "a" * 40}},
        ]
    )
    monkeypatch.setattr(deploy.time, "sleep", Mock())
    client.deploy("API", "srv-api", "a" * 40)
    assert client.request.call_args_list[0].args == (
        "/services/srv-api/deploys",
        {"commitId": "a" * 40},
    )


def test_live_wrong_commit_fails(deploy):
    client = deploy.RenderClient("secret-test-key")
    client.request = Mock(
        side_effect=[{"id": "dep-test"}, {"status": "live", "commit": {"id": "b" * 40}}]
    )
    with pytest.raises(deploy.DeploymentError, match="does not match"):
        client.deploy("API", "srv-api", "a" * 40)


def test_deploy_post_not_retried_or_error_body_logged(deploy, monkeypatch):
    request = Mock(
        side_effect=HTTPError("https://api.render.com", 503, "secret-response", {}, None)
    )
    monkeypatch.setattr(deploy, "urlopen", request)
    client = deploy.RenderClient("secret-test-key")
    with pytest.raises(deploy.DeploymentError, match="HTTP 503") as error:
        client.request("/services/srv-api/deploys", {"commitId": "a" * 40})
    assert "secret" not in str(error.value)
    request.assert_called_once()
