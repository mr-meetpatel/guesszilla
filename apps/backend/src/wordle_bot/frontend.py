"""Serve the built admin SPA on the same origin as the API."""

from pathlib import Path

from flask import Flask, Response, abort, send_from_directory


def register_frontend(app: Flask, directory: str) -> None:
    root = Path(directory).resolve()
    if not (root / "index.html").is_file():
        raise ValueError("FRONTEND_DIR must contain the built admin index.html")

    @app.get("/")
    def index() -> Response:
        return send_from_directory(root, "index.html")

    @app.get("/<path:path>")
    def frontend(path: str) -> Response:
        # Unknown API/webhook/health URLs must retain their normal 404 behavior.
        if ".." in path.split("/") or path.split("/", 1)[0] in {
            "api",
            "slack",
            "health",
            "send-stats",
        }:
            abort(404)
        if path.startswith("assets/"):
            return send_from_directory(root / "assets", path.removeprefix("assets/"))
        if path in {"favicon.ico", "robots.txt"}:
            return send_from_directory(root, path)
        if "." in path:
            abort(404)
        return send_from_directory(root, "index.html")
