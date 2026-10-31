"""Verified Slack ingress: store work before ACK, never call Slack in a request."""

import logging
import re
from datetime import UTC, datetime
from hmac import compare_digest
from typing import Any
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from slack_sdk.signature import SignatureVerifier
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from wordle_bot.config import Settings
from wordle_bot.domain import parse_wordle
from wordle_bot.repositories.interfaces import JobQueue
from wordle_bot.repositories.postgres import PostgresRepository, create_db_engine

logger = logging.getLogger(__name__)
SLACK_ID = re.compile(r"[A-Z][A-Z0-9]{1,63}")
TIMESTAMP = re.compile(r"\d{1,12}\.\d{1,9}")


def create_app(settings: Settings | None = None, queue: JobQueue | None = None) -> Flask:
    settings = settings or Settings()
    settings.require_slack()
    repository = PostgresRepository(create_db_engine(settings)) if queue is None else None
    queue = queue or repository
    assert queue is not None
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=128 * 1024)
    app.extensions["wordle_repository"] = repository
    if settings.admin_panel_enabled:
        assert repository is not None
        from wordle_bot.platform.api import register_api
        from wordle_bot.platform.auth import register_auth

        register_auth(app, settings, repository.engine)
        register_api(app, settings, repository.engine)
        if settings.persist_logs:
            from wordle_bot.logging_config import configure_logging
            from wordle_bot.platform.observability import install_sink

            configure_logging(settings.log_level)
            app.extensions["event_sink"] = install_sink(settings)

    @app.before_request
    def request_identity() -> None:
        g.request_id = str(uuid4())

    @app.after_request
    def response_identity(response: Response) -> Response:
        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        sink = app.extensions.get("event_sink")
        if sink and request.path.startswith("/api/") and request.path != "/api/v1/events":
            sink.record(
                "log",
                "http.request",
                "ERROR" if response.status_code >= 500 else "INFO",
                {
                    "method": request.method,
                    "route": str(request.url_rule),
                    "status": response.status_code,
                },
                g.get("request_id"),
            )
        return response

    verifier = (
        SignatureVerifier(settings.slack_signing_secret.get_secret_value())
        if settings.slack_signing_secret.get_secret_value()
        else None
    )

    @app.before_request
    def verify_slack() -> Response | None:
        if request.path in {"/slack/events", "/slack/commands", "/send-stats"}:
            if verifier is None:
                return Response(status=503)
            if request.method != "POST":
                return Response(status=405)
            if not verifier.is_valid_request(request.get_data(), dict(request.headers)):
                return Response("Invalid Slack signature", status=401)
        return None

    @app.get("/")
    @app.get("/health/live")
    def live() -> Response:
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready() -> tuple[Response, int]:
        try:
            healthy = repository is not None and repository.healthy(
                settings.heartbeat_max_age_seconds
            )
            if healthy and settings.admin_panel_enabled:
                assert repository is not None
                healthy = repository.healthy(settings.heartbeat_max_age_seconds, "scheduler")
        except SQLAlchemyError:
            healthy = False
        return jsonify(status="ready" if healthy else "unavailable"), 200 if healthy else 503

    @app.get("/thankyou")
    def thankyou() -> Response:
        return Response("Thank you for installing the Wordle bot.", mimetype="text/plain")

    @app.post("/slack/events")
    def events() -> Response:
        payload: Any = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return Response("Expected JSON object", status=400)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                return Response(status=400)
            return jsonify(challenge=challenge)
        if payload.get("type") != "event_callback":
            return Response(status=200)
        if not settings.admin_panel_enabled and payload.get("team_id") != settings.slack_team_id:
            return Response(status=403)
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") != "message":
            return Response(status=200)
        if event.get("subtype") or event.get("bot_id") or event.get("bot_profile"):
            return Response(status=200)
        managed = None
        if settings.admin_panel_enabled:
            from wordle_bot.platform.ingress import managed_channel

            assert repository is not None
            managed = managed_channel(
                repository.engine, payload.get("team_id", ""), event.get("channel", "")
            )
            if managed is None:
                return Response(status=200)
        elif event.get("channel") != settings.slack_channel_id:
            return Response(status=200)
        user, text, timestamp = event.get("user"), event.get("text"), event.get("ts")
        if not isinstance(user, str) or not SLACK_ID.fullmatch(user):
            return Response(status=200)
        if not isinstance(text, str) or parse_wordle(text) is None:
            return Response(status=200)
        if not isinstance(timestamp, str) or not TIMESTAMP.fullmatch(timestamp):
            return Response(status=200)
        try:
            datetime.fromtimestamp(float(timestamp), UTC)
        except ValueError, OverflowError, OSError:
            return Response(status=200)
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            return Response(status=400)
        assert queue is not None
        queue.enqueue(
            f"event:{payload.get('team_id', settings.slack_team_id)}:{event_id}",
            "message",
            {
                "user": user,
                "text": text,
                "ts": timestamp,
                **({"channel_id": managed.id} if managed else {}),
            },
        )
        return Response(status=200)

    @app.post("/slack/commands")
    @app.post("/send-stats")
    def commands() -> Response:
        data = request.form
        if settings.admin_panel_enabled:
            from wordle_bot.platform.ingress import command

            assert repository is not None
            return command(settings, repository.engine, data)
        if data.get("team_id") != settings.slack_team_id:
            return Response(status=403)
        password = data.get("text", "").strip()
        expected_password = settings.send_stats_password.get_secret_value()
        if data.get("command") != "/send-stats" or not compare_digest(password, expected_password):
            return jsonify(response_type="ephemeral", text="Usage: /send-stats password")
        trigger = data.get("trigger_id", "")
        if not 1 <= len(trigger) <= 128:
            return Response("Missing trigger_id", status=400)
        assert queue is not None
        queue.enqueue(
            f"command:{settings.slack_team_id}:{trigger}",
            "report",
            {
                "day": datetime.now(settings.timezone).date().isoformat(),
            },
        )
        return jsonify(
            response_type="ephemeral", text="Wordle stats queued for the tracked channel."
        )

    @app.errorhandler(SQLAlchemyError)
    def database_error(error: SQLAlchemyError) -> tuple[Response, int]:
        logger.error("Database request failed", extra={"error_code": type(error).__name__})
        return jsonify(error="Temporarily unavailable"), 503

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception) -> Response | tuple[Response, int]:
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code or 500
        logger.error("HTTP request failed", extra={"error_code": type(error).__name__})
        return jsonify(error="Internal server error"), 500

    return app
