# Wordle Stats Slack Bot

A Slack app that reacts to Wordle shares and posts a participation leaderboard when someone runs `/send-stats`.

The bot tracks one configured channel in one workspace. Each person counts once per weekday in the current Monday–Friday week; five days earns a crown. Weekend posts still receive reactions but do not increase weekly participation. Dates use `Asia/Kolkata` by default and come from the original message timestamp, so delayed processing preserves the correct day. Commands can be invoked elsewhere; reports always go to the tracked channel.


| Result | Reaction |
| --- | --- |
| 1–2 guesses | 🔥 `fire` |
| 3–6 guesses | 🎉 `tada` |
| Failed (`X/6`) | 👍 `thumbsup` |

## Architecture

Python **3.14.8** is installed locally through **pyenv**; **uv** manages dependencies and the committed lockfile. Flask and Gunicorn verify incoming Slack requests and persist work before acknowledging. A PostgreSQL-backed worker processes results and Slack API calls without Redis. Local PostgreSQL and hosted Supabase share SQLAlchemy repositories and one Alembic migration history.

```text
src/wordle_bot/
  config.py          Validated .env settings
  domain.py          Parsing, reactions, calendar rules, report formatting
  services.py        Participation and reporting behavior
  repositories/      Interfaces, models, PostgreSQL repository and durable queue
  http.py            Signed Slack endpoints and health checks
  slack_client.py    Slack API adapter
  worker.py          Claims, retry handling, heartbeats
  migrate.py         Serialized Alembic upgrades
  runtime.py         Combined process supervision
  cli.py             Startup and maintenance commands
migrations/          Versioned schema changes, packaged in the application
tests/               Unit and PostgreSQL integration tests
```

Unique constraints prevent duplicate participation and duplicate queued deliveries. Atomic claims, leases, and five bounded attempts allow recovery after restarts. Services depend on repository and Slack gateway interfaces; domain functions perform no I/O. Importing the package makes no network calls.

## Slack app setup

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) using `slack-app-manifest.yaml`. Replace both `YOUR_PUBLIC_HOST` placeholders with the public HTTPS hostname. For local work, start the app and use an HTTPS tunnel to port 8000 before enabling Event Subscriptions.
2. Install the app in your workspace. Copy the **Bot User OAuth Token** (`xoxb-...`) and **Signing Secret** into your environment.
3. Set `SLACK_TEAM_ID` to the workspace ID (`T...`) and `SLACK_CHANNEL_ID` to the tracked channel ID (`C...` or `G...`), not its name.
4. Invite the bot to that channel with `/invite @Wordle Stats`.
5. Set Event Subscriptions to `https://YOUR_PUBLIC_HOST/slack/events`, subscribing to `message.channels` and, for private channels, `message.groups`.
6. Set `/send-stats` to POST to `https://YOUR_PUBLIC_HOST/slack/commands`. Run it as `/send-stats password`, replacing `password` with your configured secret.

Required bot scopes are `channels:history`, `chat:write`, `reactions:write`, and `commands`. Add `groups:history` for private channels. Reinstall after changing scopes. Reports use user mentions and do not fetch profiles. `/send-stats` remains a signed POST alias; its old password argument and GET side effects are removed. This app uses manual single-workspace installation, without a public OAuth installation flow.

## Local setup with Docker

Install Docker Engine with Compose v2, configure a Slack app, and use a public HTTPS tunnel if Slack will reach your local machine.

```bash
cp .env.example .env
# Replace the four Slack values in .env.
docker compose up --build -d
docker compose ps
docker compose logs -f web worker
curl http://localhost:8000/health/ready
```

Compose starts PostgreSQL 17, applies migrations once, then starts separate web and worker services. HTTP is available at `http://localhost:8000`; the database is exposed only on localhost:15432 by default. Set `DB_HOST_PORT` in `.env` if that port is also occupied. The container always listens internally on 5432, so Compose service connections are unchanged. `POSTGRES_*` settings initialize the database container. Keep `DATABASE_URL` consistent for native tools and URL-encode special characters in a changed password. Changing initialization credentials does not change credentials in an existing volume.

`docker compose down` preserves the `postgres-data` volume. Use `down --volumes` only when you intend to delete local data. `make docker-up`, `make docker-logs`, and `make docker-down` wrap these commands.

## Local setup without Docker

Install [pyenv](https://github.com/pyenv/pyenv#installation), its Python build prerequisites, [uv](https://docs.astral.sh/uv/getting-started/installation/), and PostgreSQL 17 through your OS package manager. Start PostgreSQL and run these statements as a PostgreSQL administrator:

```sql
CREATE ROLE wordle LOGIN PASSWORD 'wordle-local-only';
CREATE DATABASE wordle OWNER wordle;
```

From the project directory:

```bash
pyenv install -s 3.14.8
pyenv local 3.14.8
python --version
uv sync --frozen --python "$(pyenv which python)"
cp .env.example .env
# Configure Slack credentials and DATABASE_URL.
make migrate
make dev
```

Run `make worker` in another terminal. For Gunicorn, use `make serve`; `make combined` supervises Gunicorn and the worker together. `make setup` installs/selects Python through pyenv and installs locked dependencies. Update pyenv if its definitions do not contain 3.14.8. `.python-version` is committed so pyenv and uv agree on the interpreter.

## Supabase setup

Use a standard PostgreSQL Supabase project. The app needs a database connection, without Supabase Auth, REST API keys, or a full self-hosted stack.

In the dashboard's **Connect** dialog, copy the **Session pooler** string for IPv4 networks, or a direct connection string if your host has appropriate connectivity. Replace the password, URL-encode reserved characters, and require TLS:

```dotenv
DB_BACKEND=supabase
DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
# Optional separate migration connection; defaults to DATABASE_URL.
# MIGRATION_DATABASE_URL=postgresql+psycopg://...
```

Do not use the transaction pooler on port 6543: migrations rely on a session-level advisory lock. Run native commands as above, or the standalone Supabase Compose configuration:

```bash
docker compose -f compose.supabase.yaml up --build -d
docker compose -f compose.supabase.yaml logs -f web worker
```

Migrations create a private `wordle_bot` schema. Keep it outside Supabase's exposed Data API schemas. Tables have RLS enabled without public policies, and PUBLIC schema/table privileges are revoked. Setup uses the schema owner (`postgres` on Supabase, the local application owner otherwise). A separately restricted runtime role needs explicit private-schema privileges and role-specific RLS policies; table grants alone are insufficient.

See [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Render free deployment

`render.yaml` defines one **free Docker web service** with a combined web/worker launcher and external Supabase storage. It creates neither a Render database nor a paid worker.

1. Push the branch to your Git provider and connect the repository to Render.
2. Create a **Blueprint** using `render.yaml` and confirm the service plan is **Free**.
3. Enter the TLS-enabled Supabase `DATABASE_URL` and the four Slack settings. Optionally add `MIGRATION_DATABASE_URL` in the dashboard.
4. Render builds the multistage Dockerfile. Startup migrates the database, then supervises one Gunicorn worker and one queue worker. HTTP binds to `0.0.0.0` on Render's supplied `PORT`.
5. Update both Slack request URLs to your `https://SERVICE.onrender.com` hostname.
6. Check Render logs and `/health/ready`, then test a Wordle share and `/send-stats`.

**Free-tier limitations:** Render sleeps after 15 minutes without inbound traffic and takes about a minute to wake. A command during waking can exceed Slack's three-second deadline; wait for readiness and retry. Event retries may recover missed deliveries, but long outages can lose incoming events. The worker pauses during sleep; persisted jobs resume after waking. The app does not bypass idle suspension. Supabase Free also has usage and inactivity limits.

Free Render PostgreSQL expires after 30 days, so storage stays in Supabase. Migrations run at startup instead of relying on paid pre-deploy jobs. All durable state is external; the ephemeral Render filesystem stores none of the application's records.

For reliable always-on service, use paid web and worker instances running `wordle-bot serve` and `wordle-bot worker`. The same image supports both. Compose retains this separate-service production layout.

See [Render free limits](https://render.com/docs/free) and [Supabase quotas](https://supabase.com/pricing).

## Configuration

Environment variables override `.env`. Never commit secrets or pass them into Docker build arguments. `.env` is excluded from Git and image builds.

| Variable | Default / purpose |
| --- | --- |
| `DB_BACKEND` | `local` or `supabase`; Supabase additionally validates TLS and connection mode |
| `DATABASE_URL` | Required PostgreSQL connection URL |
| `MIGRATION_DATABASE_URL` | Optional separate migration credential |
| `SLACK_BOT_TOKEN` | Required `xoxb-...` bot token for web/worker startup |
| `SLACK_SIGNING_SECRET` | Required signing secret |
| `SLACK_TEAM_ID`, `SLACK_CHANNEL_ID` | Required configured workspace and channel IDs |
| `SEND_STATS_PASSWORD` | Required password for `/send-stats password`; never logged or stored |
| `APP_TIMEZONE` | `Asia/Kolkata`; IANA timezone |
| `APP_ENV` | `production`; deployment label |
| `AUTO_MIGRATE` | `true`; migrate before startup |
| `PORT` | `8000`; Render supplies its port |
| `WEB_WORKERS` | `1`; keep one on Render free |
| `DB_POOL_SIZE` | `3` per process, no overflow |
| `WORKER_POLL_SECONDS` | `2` in `.env.example`, `5` on Render |
| `AUTO_SCHEDULE_REPORTS` | `true`; queue the weekly report automatically |
| `JOB_LEASE_SECONDS` | `120`; reclaim interrupted jobs after expiry |
| `HEARTBEAT_MAX_AGE_SECONDS` | `90`; readiness threshold |
| `WORKER_ID` | Optional stable process ID for health checks |
| `LOG_LEVEL` | `INFO` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Local Compose initialization |
| `DB_HOST_PORT` | `15432`; host port mapped to local PostgreSQL's container port 5432 |
| `ENV_FILE` | Optional Compose env-file path; defaults to `.env` |

Application JSON logs omit exception messages and request/response bodies to avoid exposing credentials. Do not enable SDK HTTP debug logging in production.

## Migrations and operations

```bash
make migrate
make jobs                       # Inspect up to 100 failed jobs without payloads
make retry JOB_ID=123            # Explicitly requeue a failed job
uv run --frozen wordle-bot jobs --status pending
docker compose exec worker wordle-bot jobs
docker compose exec worker wordle-bot retry 123
```

Alembic owns application migration history in `wordle_bot.alembic_version`. Do not manage the same tables with Supabase CLI migrations. `AUTO_MIGRATE=false` or `--skip-migrate` disables automatic upgrades, requiring an operator to run `wordle-bot migrate` first. Compose uses a migration service and starts dependent services only after success. Concurrent migration attempts serialize with a PostgreSQL advisory lock and bounded timeout.

For future changes, add a reviewed Alembic revision under `migrations/versions/`, update models alongside it, and test fresh upgrades and preservation of existing records. Startup never drops/recreates tables. Deploy backward-compatible schema changes before new processes. Back up before migrations; restore a backup for destructive rollback instead of blindly downgrading production.

Transient jobs retry with exponential backoff and jitter, honor Slack `Retry-After`, and stop after five attempts. Revoked tokens, missing scopes, and absent channel membership fail immediately. Correct the cause before explicitly retrying. `/health/live` checks HTTP; `/health/ready` checks PostgreSQL and a recent worker heartbeat.

Inbound delivery and participation are deduplicated. Outbound Slack reports have **at-least-once delivery**: a crash after Slack accepts a report but before completion is saved can duplicate it, including already sent chunks. Existing reactions count as success. There is no external exactly-once guarantee.

Job payloads retain only fields needed for Wordle processing; command tokens and response URLs are not stored. Completed jobs and participation history are retained. Define retention before large-scale use: deleting delivery keys removes historical deduplication. Obsolete heartbeat rows can be pruned independently.

Use PostgreSQL `pg_dump`/restore tooling to back up externally. Free providers may lack automatic backups. This version starts fresh without importing MySQL data. Replace the legacy committed database credential before reusing that database; removing it from this revision does not erase Git history.

## Checks and tests

```bash
make setup
make check
make test
make validate-render            # Validate using Render's official schema; needs network
# Create a separate disposable database, e.g. wordle_test.
TEST_DATABASE_URL=postgresql+psycopg://wordle:wordle-local-only@localhost:15432/wordle_test make test-integration
```

Integration tests require a database named `*_test` and truncate its application tables. Never use live data. They test migrations, concurrent delivery/claims, participation isolation, lease recovery, failed-job retry, RLS flags, and worker health. Unit tests cover parsing, reactions, calendar rules, configuration, signatures, API errors, and process supervision. CI runs both groups, lint/type checks, and a container build.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No reaction | Full valid share, configured channel ID, bot invitation, scopes, failed jobs and worker logs |
| URL verification fails | Public HTTPS URL, signing secret, running app; wake Render before configuring Slack |
| Render command timeout | Cold start; wait for readiness and rerun |
| Database failure | Password encoding, Supabase session pooler for IPv4, TLS, local database initialization |
| Readiness 503 | Database connection and recent worker heartbeat |
| Migration failure | DDL privileges and a direct/session connection; inspect configuration without printing secrets |
| Missing participation | Posting timezone/date, current week, one count per user/day |
| pyenv release missing | Update pyenv and rerun installation |
