"""Run database suites using the existing Compose db service and a separate test database."""

import argparse
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:17.11-bookworm"
SUITES = {
    "integration": ["pytest", "-m", "integration and not e2e"],
    "backend-e2e": ["pytest", "-m", "e2e"],
}


def database_name(value: str, application_database: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,57}_test", value):
        raise ValueError("TEST_DB_NAME must be a lowercase PostgreSQL name ending in _test")
    if value == application_database:
        raise ValueError("TEST_DB_NAME must differ from the Compose application database")
    return value


def compose_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if not (ROOT / ".env").exists() and not environment.get("ENV_FILE"):
        environment["ENV_FILE"] = str(ROOT / ".env.example")
    return environment


class ComposeDatabase:
    def __init__(self) -> None:
        self.command = shlex.split(os.environ.get("COMPOSE", "docker compose"))
        self.environment = compose_environment()

    def run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                [*self.command, *arguments],
                cwd=ROOT,
                env=self.environment,
                capture_output=True,
                text=True,
                check=True,
            )
        except OSError, subprocess.CalledProcessError:
            raise ValueError(
                "Cannot access the Compose db service. Check Docker and `docker compose ps db`."
            ) from None
        return result.stdout.strip()

    def prepare(self, *, create: bool) -> URL:
        config = json.loads(self.run("config", "--format", "json", "db"))["services"]["db"]
        if config.get("image") != IMAGE:
            raise ValueError(f"Compose db must use {IMAGE}")
        environment = config["environment"]
        application_database = environment["POSTGRES_DB"]
        name = database_name(os.environ.get("TEST_DB_NAME", "wordle_test"), application_database)
        container = self.run("ps", "--status", "running", "-q", "db")
        if not container:
            if not create:
                raise ValueError("Compose db is stopped. Run `make test-db-prepare` first.")
            print(f"Starting Compose db ({IMAGE})", flush=True)
            self.run("up", "-d", "--wait", "--wait-timeout", "60", "db")
        address = urlsplit("tcp://" + self.run("port", "db", "5432"))
        host = address.hostname
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        if not host or not address.port:
            raise ValueError("Compose db must publish port 5432 for host-side test runners")
        url = URL.create(
            "postgresql",
            username=environment["POSTGRES_USER"],
            password=environment["POSTGRES_PASSWORD"],
            host=host,
            port=address.port,
            database=application_database,
        )
        try:
            with psycopg.connect(
                url.render_as_string(hide_password=False),
                autocommit=True,
                connect_timeout=5,
            ) as connection:
                if connection.info.server_version != 170011:
                    raise ValueError(f"The running Compose db must use {IMAGE}")
                # Serialize test-database creation across concurrent make invocations.
                connection.execute("SELECT pg_advisory_lock(872435002)")
                exists = connection.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (name,),
                ).fetchone()
                if not exists:
                    if not create:
                        raise ValueError(
                            "Test database is missing. Run `make test-db-prepare` first."
                        )
                    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.Error:
            raise ValueError(
                "Cannot prepare the Compose test database. Check the db credentials and health."
            ) from None
        print(f"Using Compose db ({IMAGE}), database {name}", flush=True)
        return url.set(drivername="postgresql+psycopg", database=name)


def run_suite(suite: str, environment: dict[str, str]) -> None:
    uv = shlex.split(os.environ.get("UV", "uv"))
    print(f"Running {suite}", flush=True)
    if suite == "unit":
        subprocess.run(
            [*uv, "run", "--frozen", "pytest", "-m", "not integration and not e2e"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(["pnpm", "test"], cwd=ROOT, env=environment, check=True)
        return
    commands = {
        "frontend-e2e": ["pnpm", "e2e"],
    }
    command = commands.get(suite) or [*uv, "run", "--frozen", *SUITES[suite]]
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suite", choices=["all", "e2e", *SUITES, "frontend-e2e", "prepare", "check"]
    )
    args = parser.parse_args()
    try:
        url = ComposeDatabase().prepare(create=args.suite != "check")
        environment = {
            **os.environ,
            "TEST_DATABASE_URL": url.render_as_string(hide_password=False),
            "E2E_DATABASE_URL": url.render_as_string(hide_password=False),
        }
        if args.suite in {"prepare", "check"}:
            return
        suites = {
            "all": ["unit", "integration", "backend-e2e", "frontend-e2e"],
            "e2e": ["backend-e2e", "frontend-e2e"],
        }.get(args.suite, [args.suite])
        for suite in suites:
            run_suite(suite, environment)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None


if __name__ == "__main__":
    main()
