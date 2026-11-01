"""Single-origin frontend routing must not swallow APIs or expose files."""

import pytest
from flask import Flask

from wordle_bot.frontend import register_frontend


@pytest.fixture
def frontend(tmp_path):
    (tmp_path / "index.html").write_text("<html>Wordle admin</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('admin')")
    (tmp_path / "secret.env").write_text("secret")
    app = Flask(__name__)
    register_frontend(app, str(tmp_path))
    return app.test_client()


@pytest.mark.parametrize("path", ["/", "/workspaces", "/reports", "/administration"])
def test_frontend_supports_direct_navigation(frontend, path):
    response = frontend.get(path)
    assert response.status_code == 200
    assert b"Wordle admin" in response.data


def test_frontend_serves_built_assets(frontend):
    response = frontend.get("/assets/app.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/missing",
        "/slack/missing",
        "/health/missing",
        "/assets/missing.js",
        "/secret.env",
        "/assets/../secret.env",
        "/send-stats/missing",
    ],
)
def test_frontend_does_not_mask_missing_backend_routes_or_expose_files(frontend, path):
    assert frontend.get(path).status_code == 404


def test_missing_build_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="index.html"):
        register_frontend(Flask(__name__), str(tmp_path))
