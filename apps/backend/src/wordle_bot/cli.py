"""Operational commands; all production startup paths use this entry point."""

import argparse
import json
import logging
import os
import sys
from getpass import getpass

from wordle_bot.config import Settings
from wordle_bot.logging_config import configure_logging
from wordle_bot.migrate import migrate
from wordle_bot.repositories.postgres import PostgresRepository, create_db_engine
from wordle_bot.runtime import combined_commands, supervise
from wordle_bot.services import WordleService
from wordle_bot.slack_client import SlackClient
from wordle_bot.worker import Worker


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Wordle Slack bot")
    subcommands = root.add_subparsers(dest="command", required=True)
    for name in ("serve", "dev", "worker", "combined", "scheduler"):
        command = subcommands.add_parser(name)
        command.add_argument("--skip-migrate", action="store_true")
    subcommands.add_parser("migrate")
    subcommands.add_parser("health-worker")
    subcommands.add_parser("admin-bootstrap")
    subcommands.add_parser("admin-reset")
    subcommands.add_parser("import-legacy")
    subcommands.add_parser("prune")
    jobs = subcommands.add_parser("jobs")
    jobs.add_argument(
        "--status", choices=("pending", "running", "failed", "done"), default="failed"
    )
    retry = subcommands.add_parser("retry")
    retry.add_argument("job_id", type=int)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        if args.command == "migrate":
            migrate(settings)
            return
        if args.command in {"serve", "dev", "worker", "combined", "scheduler"}:
            settings.require_slack()
            if settings.auto_migrate and not args.skip_migrate:
                migrate(settings)
        if args.command == "combined":
            os.environ.setdefault("WORKER_ID", "combined-worker")
            sys.exit(supervise(combined_commands(admin_panel_enabled=settings.admin_panel_enabled)))
        if args.command == "serve":
            executable = os.path.join(os.path.dirname(sys.executable), "gunicorn")
            os.execv(
                executable,
                [
                    executable,
                    "--bind",
                    f"0.0.0.0:{settings.port}",
                    "--workers",
                    str(settings.web_workers),
                    "--threads",
                    "2",
                    "--timeout",
                    "30",
                    "--graceful-timeout",
                    "20",
                    "--error-logfile",
                    "-",
                    "wordle_bot.http:create_app()",
                ],
            )
        if args.command == "dev":
            from wordle_bot.http import create_app

            create_app(settings).run(host="127.0.0.1", port=settings.port, debug=False)
            return
        engine = create_db_engine(settings)
        try:
            if (
                settings.admin_panel_enabled
                and settings.persist_logs
                and args.command in {"worker", "scheduler"}
            ):
                from wordle_bot.platform.observability import install_sink

                install_sink(settings)
            repository = PostgresRepository(engine)
            if args.command in {"admin-bootstrap", "admin-reset"}:
                from wordle_bot.platform.auth import bootstrap_admin, reset_admin

                password = getpass("New admin password: ")
                if password != getpass("Confirm password: "):
                    raise ValueError("Passwords do not match")
                operation = bootstrap_admin if args.command == "admin-bootstrap" else reset_admin
                operation(engine, input("Admin username: "), password)
                print("Admin credentials updated.")
            elif args.command == "import-legacy":
                from wordle_bot.platform.ingress import import_legacy

                import_legacy(settings, engine)
            elif args.command == "prune":
                from wordle_bot.platform.api import prune

                prune(engine)
            elif args.command == "scheduler":
                from wordle_bot.platform.scheduler import Scheduler

                Scheduler(settings, engine).run()
            elif args.command == "worker":
                slack = SlackClient(settings.slack_bot_token.get_secret_value())
                Worker(settings, repository, WordleService(settings, repository, slack)).run()
            elif args.command == "health-worker":
                sys.exit(
                    0
                    if repository.healthy(
                        settings.heartbeat_max_age_seconds, os.environ.get("WORKER_ID")
                    )
                    else 1
                )
            elif args.command == "jobs":
                print(json.dumps(repository.jobs(args.status), indent=2))
            elif args.command == "retry":
                sys.exit(0 if repository.retry(args.job_id) else 1)
        finally:
            engine.dispose()
    except Exception as error:
        # Configuration and connection exceptions may include secrets: don't print repr/traceback.
        logger = logging.getLogger(__name__)
        if isinstance(error, ValueError) and str(error).startswith(
            ("Set required Slack settings:", "Set required platform settings:")
        ):
            logger.error(
                "Command failed", extra={"error_code": "configuration", "error_message": str(error)}
            )
            print(str(error), file=sys.stderr)
        else:
            logger.error("Command failed", extra={"error_code": type(error).__name__})
            print(
                "Command failed. Check configuration, database connectivity, and application logs.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
