# Wordle Control

A React super-admin platform for a multi-workspace Wordle Slack bot. Track participation, configure royalty rules, preview reports, schedule deliveries, and inspect jobs, receipts, logs and administrator actions.

The bot still reacts to Wordle shares and accepts `/send-stats password`. Each eligible person counts once per local day. The default reporting period is Monday–Friday, with five participated days earning royalty status.

## Repository

```text
apps/backend/    Flask API, Slack integration, worker, scheduler, Alembic and pytest
apps/admin/      React + TypeScript + Vite admin application and browser tests
infra/           Frontend Dockerfile, Nginx configuration and generated OpenAPI contract
scripts/         Contract generation, validation and isolated browser-test setup
```

Python 3.14.8 is installed through pyenv. uv owns the Python workspace and lockfile; pnpm owns the frontend workspace and lockfile. The supported frontend toolchain is pinned in `.node-version` and `package.json`.

## Quick start with Docker

Prerequisites: Docker Engine with Compose v2, plus pyenv/uv for the secret-generation helper. For a fresh installation:

```bash
cp .env.example .env
uv sync --frozen
make secrets
# Copy the generated JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEYS into .env.
# Configure Slack credentials and PUBLIC_URL. Do not commit .env.
docker compose up --build -d
make admin-bootstrap
```

Open http://localhost:8080 and sign in with the administrator you created. No default account or password exists. On an existing installation, add the new platform settings to the existing `.env`; do not overwrite its credentials.

Compose starts PostgreSQL, runs migrations once, then starts the API, worker, scheduler and frontend proxy. The proxy serves the React app and routes APIs and Slack callbacks under the same origin. The API binds only to localhost:8000, the frontend to localhost:8080, and PostgreSQL to localhost:15432. Change API_PORT, FRONTEND_PORT or DB_HOST_PORT to resolve host port conflicts; internal database connections remain db:5432.

```bash
make logs
make health
make admin-reset
docker compose down
```

Normal `down` preserves the database volume. `down --volumes` deletes it. Never use that flag to upgrade an existing installation.

Production requires HTTPS, COOKIE_SECURE=true and an always-on worker and scheduler. Terminate TLS at your ingress/load balancer and set PUBLIC_URL to the externally visible HTTPS origin. New schedules are paused until explicitly enabled.

## Local development without Docker

Install PostgreSQL 17, pyenv with Python build prerequisites, uv, Node matching `.node-version`, and pnpm matching `package.json`. Create a database owned by the application login:

```sql
CREATE ROLE wordle LOGIN PASSWORD 'choose-a-local-password';
CREATE DATABASE wordle OWNER wordle;
```

```bash
make setup
# Configure .env; native PostgreSQL usually uses localhost:5432.
make secrets
# Add the generated values to .env.
uv run wordle-bot migrate
uv run wordle-bot admin-bootstrap
make dev-api
```

In separate terminals run `make dev`, `make worker`, and `make scheduler`. The Vite app defaults to http://localhost:5173; set PUBLIC_URL to that origin for development. Set COOKIE_SECURE=false only for local HTTP. Native tools connecting to the Compose database should use localhost:15432 instead.

`make setup` selects the pinned interpreter with `pyenv local`; uv installs locked dependencies using that interpreter. `.python-version` remains committed. Update pyenv definitions if the pinned version is unavailable.

## Slack app and workspaces

Use `slack-app-manifest.yaml`, replacing YOUR_PUBLIC_HOST with your HTTPS hostname. Configure:

| Setting | Public URL |
| --- | --- |
| Events API | https://YOUR_HOST/slack/events |
| Slash command `/send-stats` | https://YOUR_HOST/slack/commands |
| OAuth redirect | https://YOUR_HOST/slack/oauth/callback |

Copy the Slack app's signing secret, OAuth client ID and client secret into server configuration. Enable Incoming Webhooks in the Slack app settings (the manifest includes this setting and the `incoming-webhook` scope), then reinstall/reconnect the app. In the panel choose Workspaces → Connect workspace. Select the channel on Slack's authorization screen and authorize the app. Only that selected channel is saved; the app never imports the workspace channel list. Back in the panel, set its slash-command password. Invite the bot to the channel if it is not already a member. To connect another channel, repeat authorization and select that channel. Refresh connected channels updates saved channel names only, including when an old discovery job runs after upgrading. Existing stored channels/history are preserved on upgrade. The worker must be running for message processing.

People join the tracked channel's roster automatically on their first valid Wordle submission, with their Slack display name. Duplicate events do not create duplicate participants or credited days, and existing participant exclusions are preserved. Regular messages do not register players. Sync roster remains available if you want all human channel members included, including people who have never played.

Scopes include channels:read/history, groups:read/history, users:read, chat:write, reactions:write, commands, [incoming-webhook](https://docs.slack.dev/reference/scopes/incoming-webhook/) for Slack's channel picker and [metadata.message:read](https://docs.slack.dev/reference/scopes/metadata.message.read/) for delivery reconciliation. The OAuth webhook URL is not stored or used; reports still use the bot API and its delivery ledger. Private channels require an invitation. Reinstall after adding scopes. Tokens are encrypted in PostgreSQL and never returned to the frontend. One Slack app can connect multiple workspaces; callbacks are routed using the signed workspace identity.

Managed channels use their own hashed slash-command passwords. Invoke `/send-stats YOUR_PASSWORD` inside a tracked channel. A command from another channel is accepted only when that workspace has exactly one active destination. Manual report requests share scheduled-report deduplication.

To preserve an existing environment-configured installation:

```bash
make import-legacy
```

This explicitly imports its workspace/channel, encrypts its existing bot token, hashes its command password, and binds pending legacy jobs to that channel. It preserves participation dates. Sync the roster after reconnecting/reinstalling with the new scopes. Legacy data never stored UTC timestamps, membership snapshots or Slack receipts; those cannot be reconstructed. Historical delivery suppression starts with the new report ledger.

## Supabase

Set DB_BACKEND=supabase and use a TLS-enabled PostgreSQL connection URL from Supabase's Connect dialog. Use a direct connection or session pooler on port 5432; IPv4-only deployments should use the session pooler. Transaction pooling on port 6543 is rejected because migrations use a session advisory lock.

```dotenv
DB_BACKEND=supabase
DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
# Optional separate migration credential:
# MIGRATION_DATABASE_URL=postgresql+psycopg://...
```

```bash
docker compose -f compose.supabase.yaml up --build -d
docker compose -f compose.supabase.yaml exec web wordle-bot admin-bootstrap
```

Alembic manages the same private wordle_bot schema on both backends. Tables have RLS enabled and no public access policies. Keep the schema outside the Supabase Data API. Migrations and the default runtime use the schema owner; deploying a restricted runtime role additionally requires explicit privileges and role-specific RLS policies. Never expose database or encryption credentials as VITE variables.

## Reports and participation

Channel settings control timezone, eligible weekdays, royalty threshold, announcement, title and reaction mapping. The default reactions are fire for 1–2 guesses, tada for 3–6 and thumbsup for X. Future participation dates derive from UTC Slack timestamps in the configured timezone; legacy dates are preserved as recorded.

Sync human channel members, include/exclude participants, then inspect the calendar. The participation ratio is eligible roster members with at least one credited day divided by eligible roster size. An empty roster displays no ratio rather than dividing by zero. Players are sorted case-insensitively by display name.

Reports have an immutable snapshot of their roster, names, settings version, date range, counts, cutoff, destination and rendered parts. Changes affect new evaluations. Repeated requests for the same channel/date range reuse the latest report; an intentional resend requires confirmation and the expected revision. Competing resends and stale settings edits receive conflicts instead of silently overwriting each other.

Manual date ranges are inclusive and limited to 366 days. Publishing rejects a royalty threshold greater than the eligible days in the selected range; analytics can still preview such ranges. Default periods on all analytics/report screens and current/previous-week windows follow the selected weekdays in an ISO calendar week. Selecting all seven days gives Monday–Sunday; changing weekdays or timezone updates the default period. Rolling windows end on the intended occurrence's reporting date. Existing report snapshots remain unchanged.

## Scheduling and delivery guarantees

Create daily or weekly schedules with local time, IANA timezone and current-week, previous-week or trailing-day windows. The panel previews upcoming times and records run history. Daily schedules using an unchanged weekly range will reuse the existing report; choose a rolling window when each day's range should differ.

Scheduler replicas lock due rows and atomically record an occurrence, create/reuse a report and advance the cursor. A unique schedule/UTC-occurrence key handles races. After an outage only the latest missed occurrence within 24 hours is caught up; older occurrences are recorded without posting. DST overlaps run once at the first occurrence; nonexistent local times move to the next valid instant. Container timezone calculations use the locked tzdata dependency.

The outbox stores each ordered message part before sending. Confirmed parts retain Slack timestamps and are never reposted. A crash during sending, transport timeout or ambiguous server error becomes uncertain. The worker searches Slack history for a matching non-secret delivery marker and bot identity. If confirmation is unavailable, the delivery stays uncertain for admin review; it is never blindly resent. A blocked part also blocks later parts.

Slack and PostgreSQL cannot participate in one atomic transaction. This design prioritizes avoiding duplicate posts over automatically resending uncertain work. Rate limits and confirmed rejections can be retried; unknown outcomes cannot. An intentional admin resend is a new report revision. Deduplication identity records survive cleanup.

Use Reports → View delivery for receipts, safe retries and history reconciliation. Uncertain deliveries cannot use generic job retries or trigger a new report revision until resolved. If Slack history or metadata is unavailable, restore permissions and retry reconciliation; do not repeatedly post the message manually.

## Authentication and operations

All admin APIs require cookie JWTs and database-backed active-session checks. Access tokens expire in five minutes. Refresh JWTs rotate with a seven-day absolute session lifetime; replay revokes the family. Browser mutations require CSRF tokens. Tokens are HttpOnly; none are kept in localStorage. Logout, password changes, account disablement and admin revocation invalidate sessions across replicas.

Passwords use Argon2id. Login throttling is shared through PostgreSQL. New/reset admin credentials must be changed on first login. All initial admins have global access. Public registration is disabled, and the last active super-admin cannot be disabled.

`make admin-reset` provides interactive operator recovery. For native development use `uv run wordle-bot admin-reset`. Administrative actions and session changes are audited. Never put passwords into command-line arguments.

Logs and audit events are available in the panel with filters, live tail, and export of the visible records. Canonical audit/job/delivery events are transactional. Application logs use a bounded asynchronous redacted sink, with stdout retained when the database is unavailable. Secrets, request bodies, command passwords and token values are not included in these events.

Health shows database connectivity, worker/scheduler heartbeats, queue state and uncertain sends. Minimal `/health/live` and `/health/ready` probes are public and expose no operational details. Panel monitoring cannot replace stdout or an external availability probe during a complete database outage.

The scheduler performs daily cleanup: application logs 14 days, detailed job/delivery events 90 days and audits 365 days. `make prune` runs cleanup manually. Report snapshots, receipts and deduplication keys are retained. Configure database backups and storage monitoring for your hosting provider.

```bash
make jobs
make retry JOB_ID=123
make logs
make health
make prune
```

Report jobs must use the panel's receipt-aware delivery controls, not generic retry. No Docker socket, arbitrary SQL console or remote shell is exposed in the panel.

## Configuration

| Variable | Purpose |
| --- | --- |
| ADMIN_PANEL_ENABLED | Enables managed workspace/admin mode; Compose sets true |
| JWT_SECRET_KEY | Random signing secret, at least 32 characters |
| CREDENTIAL_ENCRYPTION_KEYS | Comma-separated Fernet keyring; newest key first |
| PUBLIC_URL | External frontend origin, used for OAuth and login origin checks |
| COOKIE_SECURE | true for HTTPS; false only for local HTTP |
| SLACK_CLIENT_ID / SLACK_CLIENT_SECRET | Slack OAuth app credentials |
| SLACK_SIGNING_SECRET | Shared Slack app request verifier |
| SLACK_BOT_TOKEN / SLACK_TEAM_ID / SLACK_CHANNEL_ID / SEND_STATS_PASSWORD | Legacy import or standalone compatibility mode |
| DATABASE_URL / MIGRATION_DATABASE_URL | Runtime/migration PostgreSQL credentials |
| DB_BACKEND | local or supabase |
| APP_TIMEZONE | Default timezone for compatibility mode; managed channels have their own |
| WORKER_POLL_SECONDS / JOB_LEASE_SECONDS | Queue polling interval and recovery lease |
| WEB_WORKERS / DB_POOL_SIZE | Per-service processes and bounded per-process connection pool |
| PERSIST_LOGS | Enables asynchronous application log persistence |
| FRONTEND_PORT / API_PORT / DB_HOST_PORT | Local Compose host port mappings |

All environment values override `.env`. Back up encryption keys alongside the database. To rotate signing keys, revoke sessions and deploy a new key to all API replicas in a coordinated rollout. For credential encryption, deploy a keyring containing the new key first and old keys after it; retain the old keys until credentials have been re-encrypted/reconnected and every replica has restarted.

## TDD and validation

Tests reuse the existing Compose `db` service running `postgres:17.11-bookworm`.
Install dependencies with `make setup`, then run:

```bash
make test-browser-install  # once: install Playwright Chromium
make test-all
```

`test-all` runs backend unit tests, frontend unit tests, backend integration tests,
backend E2E workflows and frontend browser E2E tests in sequence, stopping on failure.
The runner reuses a running `db` container, or starts that same Compose service if it is
stopped. It creates `wordle_test` inside that PostgreSQL instance if needed. Credentials
come from the Compose configuration and `.env`; the host port is discovered automatically.
No URL exports, native PostgreSQL installation or separate test database container are
required. Tests run locally through uv/pnpm; browser test servers start automatically.
CI uses the same Compose service and commands.

The test database must differ from `POSTGRES_DB` and end in `_test`. Application tables
are reset only inside that test database; the application's database is preserved.
Use `make test-db-prepare` to prepare it separately, or
`make test-all TEST_DB_NAME=wordle_ci_test` to choose another test database on the same
container. The generated URLs replace inherited `TEST_DATABASE_URL`/`E2E_DATABASE_URL`
for test subprocesses, so old exports cannot send these Make targets to native PostgreSQL.

Individual commands:

| Command | Runs |
| --- | --- |
| `make test` / `make test-unit` | Backend and frontend unit tests; no database needed |
| `make test-backend-unit` | Backend unit tests |
| `make test-frontend-unit` | Frontend unit tests |
| `make test-integration` | PostgreSQL integration tests |
| `make test-backend-e2e` | Slack ingress → queue → worker/outbox → admin receipts, plus runtime lifecycle checks |
| `make test-frontend-e2e` | Playwright browser tests with a seeded API and Vite frontend |
| `make test-e2e` | Backend and frontend E2E suites |
| `make test-db-prepare` | Reuse/start Compose db and create the separate test database |
| `make test-db-check` | Check the running Compose db and existing test database |
| `make test-all` | All test suites |

```bash
make check
make api-client
make check-client
```

Integration and E2E suites reset application tables in the separate *_test database.
Never point them at live data or run them simultaneously against the same database.
Make serializes these suites even with `-j`. Backend E2E mocks outbound Slack; browser
tests start a seeded API and frontend without a Slack worker. No live Slack credentials
are needed, and tests cannot send messages to Slack.

The suite covers parser/regression behavior, signatures/password checking, date windows, real PostgreSQL races, refresh replay/revocation, CSRF, OAuth state validation, immutable snapshots, missed schedules, outbox crash recovery, login, roster display, manual deduplication, schedule history and audit/session screens.

Backend OpenAPI is generated from safe response schemas. The React client imports its generated resource types. CI rejects contract drift, requires PostgreSQL integration tests, runs browser workflows and builds both runtime images.

## Rollout and recovery

1. Back up the existing database and preserve its original credentials and encryption keys.
2. Build images, apply additive migrations through the one-shot migration service and bootstrap a named administrator.
3. Import the existing installation if needed, or connect through OAuth; sync the roster and inspect previews.
4. Confirm local/HTTPS cookie configuration, health and logs. Enable schedules explicitly only after review.
5. Use safe delivery reconciliation after interrupted sends. Keep snapshots and receipt records intact during recovery.

Use PostgreSQL pg_dump/restore tooling for backups. A destructive schema rollback requires a tested restore or reviewed down migration. Routine restarts/redeployments preserve data and pending work.

The supported production layout is separate always-on API, worker, scheduler and frontend services. Render Free sleeping cannot meet reliable scheduling; `render.yaml` targets paid compute with external Supabase. Supply the common secrets consistently to all backend services and set the frontend's PUBLIC_URL as the Slack redirect origin.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API exits at startup | Generate real JWT/Fernet keys; placeholders are rejected |
| Login works but sessions disappear | COOKIE_SECURE and the browser's actual HTTP/HTTPS origin |
| Login rejected from local Vite | PUBLIC_URL must match the Vite origin, usually http://localhost:5173 |
| Missing roster/names | Bot invitation, users:read scope, reinstall and roster sync |
| Schedule runs but report is invalid | Window contains enough eligible days for its royalty threshold |
| Report stays uncertain | Restore history permissions and reconcile; do not blindly resend |
| No new report for a second schedule | The same channel/date range already has a report; inspect its revision |
| Worker/scheduler unhealthy | Database connectivity, migrations, heartbeat and stdout logs |
| Port already in use | Set DB_HOST_PORT, API_PORT or FRONTEND_PORT to another free host port |
| Supabase connection fails | IPv4 session pooler, URL-encoded password, TLS and DDL privileges |
