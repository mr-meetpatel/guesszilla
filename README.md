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

### Storage cleanup

To reset a workspace's game data, pause it and choose **Clear game history** on its workspace card. Enter its workspace ID and your current admin password. This permanently removes attempts, participants, reports, receipts, idempotency records, related jobs/events and schedule run history. Connections, channel configuration, passwords and schedule definitions are retained; schedules are paused. Running jobs or sending/uncertain deliveries block cleanup. Resolve these through the normal job/delivery controls before retrying. Deleting historical receipts resets duplicate-report protection for those periods. Messages already sent to Slack are not removed. Resume the workspace and explicitly re-enable schedules when ready.

In **Administration → Event storage → Clear all events**, type `DELETE EVENTS` and enter your current admin password to clear global event/log/audit history. One cleanup audit record is retained and new events continue to be recorded. Neither action removes administrator accounts or sessions. Cleanup runs in a transaction; the endpoints require JWT authentication and CSRF protection. Slack client ID and secret remain configured through `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` in the server environment.

For installations that ran the withdrawn database-backed OAuth settings revision, migration `0004` removes its settings table and OAuth credential column. Fresh installations never create that storage. Configure the Slack app credentials in the environment before upgrading; workspace bot connections and game history are preserved.

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
| PERSIST_LOGS | Enables database log/audit persistence (default: `false`) |

The admin frontend also hides the Logs & audit screen by default. Set
`VITE_PERSIST_LOGS=true` at frontend build time to expose it when backend
`PERSIST_LOGS=true` is enabled.
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

## Render CI/CD

Pull requests run CI only. After a merge (or direct push) to `main`, the same workflow
deploys only after **all checks pass**. It deploys the tested commit SHA in this order:
API → worker → scheduler → admin frontend. Each service must become live before the
next deploy starts. Failed builds, failed startups and timeouts fail the deployment job.
Production deployments are serialized; an active release is allowed to finish.

One-time setup:

1. Create a Render Blueprint from this repository using `render.yaml` on `main`.
   It provisions **four paid Starter services**. Review those costs before applying.
   Existing services can also be used; set their linked branch to `main` and
   **Auto-Deploy to Off** so GitHub controls releases without duplicate deploys.
2. Configure runtime secrets in Render's `wordle-platform-common` environment group:
   `DATABASE_URL`, `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEYS`,
   `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and `PUBLIC_URL`.
   `PUBLIC_URL` is the admin frontend's HTTPS URL. If migration and runtime credentials
   differ, also add `MIGRATION_DATABASE_URL`. Keep existing keys on redeployment.
   Application secrets stay in Render; GitHub needs only deployment credentials.
3. Create a Render API key in Account Settings and copy each service ID (`srv-...`)
   from its dashboard URL. Create a GitHub environment named `production` using the
   repository Settings → Environments page, or the shell commands below.
4. Add `RENDER_API_KEY` as an environment **secret** and the four service IDs as
   environment **variables** in GitHub's `production` environment.

Configure GitHub from a terminal with the GitHub CLI (`gh auth login` first):

```bash
gh api --method PUT repos/{owner}/{repo}/environments/production
gh secret set RENDER_API_KEY --env production  # interactive, hidden input
gh variable set RENDER_API_SERVICE_ID --env production --body 'srv-YOUR_API_ID'
gh variable set RENDER_WORKER_SERVICE_ID --env production --body 'srv-YOUR_WORKER_ID'
gh variable set RENDER_SCHEDULER_SERVICE_ID --env production --body 'srv-YOUR_SCHEDULER_ID'
gh variable set RENDER_ADMIN_SERVICE_ID --env production --body 'srv-YOUR_ADMIN_ID'
```

Protect `main` with a rule requiring the `checks` job before merging. GitHub environment
approval rules are optional; without required reviewers a successful `main` build deploys
automatically. Do not require the deployment job on PRs: it intentionally skips them.
Initial Blueprint provisioning deploys services once; subsequent releases use GitHub.

For a manual deploy from your shell using the same script:

```bash
cp .env.render.example .env.render
# Edit .env.render with your Render API key and four service IDs.
# This file is ignored by Git. Never commit deployment credentials.
set -a
source .env.render
set +a
git fetch origin main
export RENDER_COMMIT_SHA=$(git rev-parse origin/main)
# Confirm CI passed for this SHA before manually deploying it.
make deploy-render
```

The local command requires uv and the project's Python setup (`make setup`). It does
not run tests or check GitHub results; automatic deployments enforce CI through
`needs: checks`. Avoid a manual deploy while the GitHub production job is running.
Neither command creates services or replaces their runtime environment variables.

API startup runs advisory-lock-protected migrations before serving; deploys stop if
that startup fails. Use additive migrations compatible with the previous release,
because services roll out sequentially. A failed release can leave services on mixed
versions; inspect Render before retrying. The script never automatically repeats a
deployment POST or rolls back database migrations. Inspect both GitHub Actions and
Render deploy logs, fix the failure, then deploy a known tested commit again.

Render API reference: [deploy a specific commit](https://render.com/docs/deploys#deploying-a-specific-commit)
and [disable automatic deploys](https://render.com/docs/blueprint-spec#autodeploytrigger).

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
