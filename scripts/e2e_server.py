"""Isolated browser-test API: destructive only to an explicit *_test database.

No scheduler or worker runs here, so browser tests cannot call Slack.
"""

import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from wordle_bot.config import Settings
from wordle_bot.http import create_app
from wordle_bot.migrate import migrate
from wordle_bot.platform.auth import bootstrap_admin, passwords
from wordle_bot.platform.models import Channel, Participant, Workspace
from wordle_bot.platform.reports import crypto
from wordle_bot.platform.rules import ReportRules
from wordle_bot.repositories.models import Base
from wordle_bot.repositories.postgres import PostgresRepository, create_db_engine

url = os.environ.get("E2E_DATABASE_URL", "")
if not url or not (make_url(url).database or "").endswith("_test"):
    raise SystemExit("E2E_DATABASE_URL must point to a disposable database ending in _test")
settings = Settings(
    _env_file=None,
    database_url=SecretStr(url),
    admin_panel_enabled=True,
    jwt_secret_key=SecretStr("browser-tests-only-not-a-production-signing-key"),
    credential_encryption_keys=SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    cookie_secure=False,
    public_url="http://127.0.0.1:18081",
    persist_logs=False,
)
migrate(settings)
engine = create_db_engine(settings)
with engine.begin() as connection:
    names = ",".join(f"wordle_bot.{t.name}" for t in Base.metadata.sorted_tables)
    connection.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
bootstrap_admin(engine, "admin", "test-admin-password")
with Session(engine) as db, db.begin():
    db.add(
        Workspace(
            id="TDEMO",
            name="Demo workspace",
            bot_id="BDEMO",
            encrypted_token=crypto(settings).encrypt(b"fake-token-never-used").decode(),
        )
    )
    db.flush()
    channel = Channel(
        workspace_id="TDEMO",
        slack_id="CDEMO",
        name="wordle",
        active=True,
        rules=ReportRules().model_dump(),
        password_hash=passwords.hash("test-channel-password"),
    )
    db.add(channel)
    db.flush()
    db.add_all(
        Participant(channel_id=channel.id, user_id=user, name=name)
        for user, name in (("UAMY", "Amy"), ("UBOB", "Bob"), ("UCHARLIE", "Charlie"))
    )
    channel.roster_synced_at = datetime.now(UTC)
today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
monday = today - timedelta(days=today.weekday())
repository = PostgresRepository(engine)
for user, days in (("UAMY", 5), ("UBOB", 3)):
    for offset in range(days):
        repository.record("TDEMO", "CDEMO", user, monday + timedelta(days=offset))
app = create_app(settings)
app.run(host="127.0.0.1", port=18080, debug=False)
