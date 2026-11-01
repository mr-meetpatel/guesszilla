"""Single migration entry point for local PostgreSQL and Supabase."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from wordle_bot.config import Settings
from wordle_bot.repositories.postgres import create_db_engine


def migration_directory() -> Path:
    packaged = Path(__file__).parent / "migrations"
    return packaged if packaged.exists() else Path(__file__).resolve().parents[2] / "migrations"


def migrate(settings: Settings) -> None:
    engine = create_db_engine(settings, migration=True)
    config = Config()
    config.set_main_option("script_location", str(migration_directory()))
    try:
        with engine.connect() as connection:
            # Session-level lock survives Alembic's transaction commits. Bound waits to 60s.
            connection.execute(text("SET statement_timeout = '60s'"))
            connection.execute(text("SELECT pg_advisory_lock(872434981)"))
            connection.commit()
            try:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(872434981)"))
                connection.commit()
    finally:
        engine.dispose()
