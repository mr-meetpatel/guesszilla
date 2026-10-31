"""Slack installation and background roster synchronization."""

import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from flask import Blueprint, abort, redirect, request
from slack_sdk import WebClient
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wordle_bot.config import Settings
from wordle_bot.platform.auth import audit, digest
from wordle_bot.platform.models import (
    Admin,
    AdminSession,
    Channel,
    OAuthState,
    Participant,
    Workspace,
    utcnow,
)
from wordle_bot.platform.reports import crypto
from wordle_bot.platform.rules import ReportRules

SCOPES = (
    "channels:read,channels:history,groups:read,groups:history,users:read,"
    "chat:write,reactions:write,commands,metadata.message:read"
)


class SlackInstallations:
    def __init__(
        self, settings: Settings, engine: Engine, progress: Callable[[], bool] | None = None
    ) -> None:
        self.settings, self.engine = settings, engine
        self.progress = progress

    def checkpoint(self) -> None:
        if self.progress is not None and not self.progress():
            raise ValueError("Job lease was lost")

    def start(self, session_id: str) -> str:
        if (
            not self.settings.slack_client_id
            or not self.settings.slack_client_secret.get_secret_value()
        ):
            abort(409, description="Configure the Slack OAuth client before connecting a workspace")
        state = secrets.token_urlsafe(32)
        with Session(self.engine) as db, db.begin():
            db.add(
                OAuthState(
                    digest=digest(state),
                    session_id=session_id,
                    expires_at=utcnow() + timedelta(minutes=10),
                )
            )
        return "https://slack.com/oauth/v2/authorize?" + urlencode(
            {
                "client_id": self.settings.slack_client_id,
                "scope": SCOPES,
                "redirect_uri": self.settings.public_url.rstrip("/") + "/slack/oauth/callback",
                "state": state,
            }
        )

    def register(self, app: Any) -> None:
        blueprint = Blueprint("slack_oauth", __name__)

        @blueprint.get("/slack/oauth/callback")
        def callback() -> Any:
            with Session(self.engine) as db, db.begin():
                state = db.scalar(
                    select(OAuthState)
                    .where(OAuthState.digest == digest(request.args.get("state", "")))
                    .with_for_update()
                )
                if not state or state.consumed or state.expires_at <= utcnow():
                    abort(400, description="OAuth state is invalid or expired")
                session = db.get(AdminSession, state.session_id)
                admin = db.get(Admin, session.admin_id) if session else None
                if (
                    not session
                    or session.revoked
                    or session.expires_at <= utcnow()
                    or not admin
                    or not admin.active
                ):
                    abort(401)
                state.consumed = True
                actor = admin.id
            if request.args.get("error") or not request.args.get("code"):
                return redirect("/workspaces?oauth=cancelled")
            result = WebClient(timeout=10, retry_handlers=[]).oauth_v2_access(
                client_id=self.settings.slack_client_id,
                client_secret=self.settings.slack_client_secret.get_secret_value(),
                code=request.args["code"],
                redirect_uri=self.settings.public_url.rstrip("/") + "/slack/oauth/callback",
            )
            team = result.get("team")
            if not team or not team.get("id") or not result.get("access_token"):
                abort(422, description="A workspace bot installation is required")
            with Session(self.engine) as db, db.begin():
                workspace = db.get(Workspace, team["id"])
                if not workspace:
                    workspace = Workspace(id=team["id"], name=team["name"], encrypted_token="")
                    db.add(workspace)
                workspace.encrypted_token = (
                    crypto(self.settings).encrypt(result["access_token"].encode()).decode()
                )
                workspace.name, workspace.active = team["name"], True
                workspace.bot_id = result.get("bot_user_id", "")
                workspace.scopes = result.get("scope", "")
                workspace.updated_at = utcnow()
                audit(db, "workspace.connected", actor=actor, workspace=workspace.id)
            return redirect("/workspaces?oauth=connected")

        app.register_blueprint(blueprint)

    def client(self, workspace_id: str) -> WebClient:
        with Session(self.engine) as db:
            workspace = db.get(Workspace, workspace_id)
            if not workspace or not workspace.active:
                raise ValueError("Workspace unavailable")
            token = crypto(self.settings).decrypt(workspace.encrypted_token.encode()).decode()
        return WebClient(token=token, timeout=10, retry_handlers=[])

    def discover(self, workspace_id: str) -> None:
        client = self.client(workspace_id)
        self.checkpoint()
        identity = client.auth_test()
        if identity.get("team_id") != workspace_id or not identity.get("user_id"):
            raise ValueError("Workspace bot identity could not be verified")
        with Session(self.engine) as db, db.begin():
            workspace = db.get(Workspace, workspace_id)
            if not workspace or not workspace.active:
                raise ValueError("Workspace unavailable")
            workspace.bot_id = identity["user_id"]
        page: Any
        cursor = None
        while True:
            self.checkpoint()
            page = client.conversations_list(
                types="public_channel,private_channel",
                limit=200,
                exclude_archived=True,
                cursor=cursor,
            )
            with Session(self.engine) as db, db.begin():
                for item in page.get("channels", []):
                    db.execute(
                        insert(Channel)
                        .values(
                            workspace_id=workspace_id,
                            slack_id=item["id"],
                            name=item["name"],
                            active=False,
                            rules=ReportRules().model_dump(),
                        )
                        .on_conflict_do_update(
                            constraint="uq_workspace_channel", set_={"name": item["name"]}
                        )
                    )
            cursor = page.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    def sync_roster(self, channel_id: str) -> None:
        with Session(self.engine) as db:
            channel = db.get(Channel, channel_id)
            if not channel:
                raise ValueError("Channel unavailable")
            workspace_id, slack_id = channel.workspace_id, channel.slack_id
        client = self.client(workspace_id)
        page: Any
        members: set[str] = set()
        cursor = None
        while True:
            self.checkpoint()
            page = client.conversations_members(channel=slack_id, limit=200, cursor=cursor)
            members.update(page.get("members", []))
            cursor = page.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        profiles: dict[str, str] = {}
        cursor = None
        while True:
            self.checkpoint()
            page = client.users_list(limit=200, cursor=cursor)
            for user in page.get("members", []):
                if (
                    user["id"] in members
                    and not user.get("is_bot")
                    and not user.get("is_app_user")
                    and not user.get("deleted")
                    and user["id"] != "USLACKBOT"
                ):
                    profile = user.get("profile", {})
                    profiles[user["id"]] = (
                        profile.get("display_name") or profile.get("real_name") or user["id"]
                    )[:160]
            cursor = page.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        self.checkpoint()
        with Session(self.engine) as db, db.begin():
            channel = db.scalar(select(Channel).where(Channel.id == channel_id).with_for_update())
            assert channel is not None
            existing = {
                p.user_id: p
                for p in db.scalars(select(Participant).where(Participant.channel_id == channel_id))
            }
            for user_id, participant in existing.items():
                participant.active = user_id in profiles
            for user_id, name in profiles.items():
                if user_id in existing:
                    existing[user_id].name = name
                else:
                    db.add(Participant(channel_id=channel_id, user_id=user_id, name=name))
            channel.roster_synced_at = utcnow()
            audit(
                db,
                "roster.synced",
                workspace=workspace_id,
                target=channel_id,
                details={"members": len(profiles)},
            )
