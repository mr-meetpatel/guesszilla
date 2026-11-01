"""Validated configuration shared by HTTP, workers, and migrations."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    db_backend: Literal["local", "supabase"] = "local"
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    slack_bot_token: SecretStr = SecretStr("")
    slack_signing_secret: SecretStr = SecretStr("")
    slack_team_id: str = ""
    slack_channel_id: str = ""
    send_stats_password: SecretStr = SecretStr("")
    app_timezone: str = "Asia/Kolkata"
    app_env: Literal["development", "production"] = "production"
    auto_migrate: bool = True
    port: int = Field(default=8000, ge=1, le=65535)
    db_pool_size: int = Field(default=3, ge=1, le=20)
    web_workers: int = Field(default=1, ge=1, le=8)
    worker_poll_seconds: float = Field(default=1, ge=0.1, le=30)
    job_lease_seconds: int = Field(default=120, ge=60, le=600)
    heartbeat_max_age_seconds: int = Field(default=90, ge=30)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    admin_panel_enabled: bool = False
    jwt_secret_key: SecretStr = SecretStr("")
    credential_encryption_keys: SecretStr = SecretStr("")
    public_url: str = "http://localhost:8080"
    cookie_secure: bool = True
    # Database log/audit retention is opt-in to keep hosted free tiers small.
    persist_logs: bool = False
    slack_client_id: str = ""
    slack_client_secret: SecretStr = SecretStr("")

    @field_validator("app_timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("APP_TIMEZONE must be an IANA timezone") from None
        return value

    @model_validator(mode="after")
    def validate_database(self) -> Settings:
        for secret in (self.database_url, self.migration_database_url):
            if secret is None:
                continue
            try:
                url = make_url(secret.get_secret_value())
                if url.get_backend_name() != "postgresql" or not url.host or not url.database:
                    raise ValueError
            except Exception:
                raise ValueError("Database URLs must be valid PostgreSQL connection URLs") from None
            if self.db_backend == "supabase" and url.query.get("sslmode") not in {
                "require",
                "verify-ca",
                "verify-full",
            }:
                raise ValueError("Supabase database URLs must require TLS via sslmode")
            if self.db_backend == "supabase" and url.port == 6543:
                raise ValueError("Use a Supabase direct connection or session pooler on port 5432")
        return self

    def require_slack(self) -> None:
        if self.admin_panel_enabled:
            if len(
                self.jwt_secret_key.get_secret_value()
            ) < 32 or self.jwt_secret_key.get_secret_value().startswith("replace-"):
                raise ValueError(
                    "Set required platform settings: JWT_SECRET_KEY (at least 32 random characters)"
                )
            from cryptography.fernet import Fernet

            try:
                for key in self.credential_encryption_keys.get_secret_value().split(","):
                    Fernet(key.encode())
            except Exception:
                raise ValueError(
                    "Set required platform settings: CREDENTIAL_ENCRYPTION_KEYS (valid Fernet keys)"
                ) from None
            return
        required = {
            "SLACK_BOT_TOKEN": self.slack_bot_token.get_secret_value(),
            "SLACK_SIGNING_SECRET": self.slack_signing_secret.get_secret_value(),
            "SLACK_TEAM_ID": self.slack_team_id,
            "SLACK_CHANNEL_ID": self.slack_channel_id,
            "SEND_STATS_PASSWORD": self.send_stats_password.get_secret_value(),
        }
        missing = [
            name for name, value in required.items() if not value or "replace" in value.lower()
        ]
        if missing:
            raise ValueError(f"Set required Slack settings: {', '.join(missing)}")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)
