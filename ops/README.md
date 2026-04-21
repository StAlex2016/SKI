# Prod / Staging split — operator guide

## What changed in code

- `app/config.py` — centralized env: `APP_ENV`, `ALLOWED_USER_IDS`, `OWNER_ID`, `LOG_DIR`, `OUTPUT_DIR`, `TMP_DIR`, `REDIS_DB`.
- `app/state.py` — Redis `db` now reads `REDIS_DB` (0=prod, 1=staging).
- `app/utils/logger.py` — `LOG_DIR` env-driven.
- `app/utils/posthog_client.py` — every capture/identify tagged with `env=prod|staging`.
- `app/services/pdf_service.py` — PDFs go to `OUTPUT_DIR`.
- `app/services/pdf_detailed_service.py` — same.
- `app/main.py` — `ALLOWED_USERS`/`OWNER_ID` imported from config; video temp path uses `TMP_DIR`; `/start` welcome gets `[STAGING]` banner when `APP_ENV=staging`.
- `scripts/backup_db.sh` — parameterized: `ENV_FILE`, `BACKUP_DIR`, `BACKUP_PREFIX`, `RETENTION_DAYS`.

## One-time setup on the VPS

### 1. Create the staging Telegram bot

```
# in @BotFather:
/newbot  →  name: Alpine Ski Staging  →  username: alpineski_staging_bot
# copy the token
```

### 2. Create staging PG database (separate from prod)

```bash
sudo -u postgres psql <<'SQL'
CREATE USER skibot_staging WITH PASSWORD '<STAGING_PG_PASSWORD>';
CREATE DATABASE skibot_staging OWNER skibot_staging;
GRANT ALL PRIVILEGES ON DATABASE skibot_staging TO skibot_staging;
SQL
```

Schema is created automatically by `init_db()` on first bot start.

### 3. Create staging working dir

```bash
sudo mkdir -p /var/lib/skibot/staging/logs
sudo mkdir -p /tmp/skibot_staging
sudo chown -R ubuntu:ubuntu /var/lib/skibot /tmp/skibot_staging
```

### 4. Create env files

```bash
cp ops/env/prod.env.example    /home/ubuntu/ski-mvp-bot/.env.prod
cp ops/env/staging.env.example /home/ubuntu/ski-mvp-bot/.env.staging
chmod 600 /home/ubuntu/ski-mvp-bot/.env.*
```

Fill in the `<PLACEHOLDERS>` in each. **Critical values that MUST differ between prod & staging:**
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY` (so you can hard-cap spend on staging key in OpenAI dashboard)
- `PG_DB` / `PG_USER` / `PG_PASSWORD`
- `REDIS_DB`
- `LOG_DIR`, `OUTPUT_DIR`, `TMP_DIR`
- (recommended) separate `POSTHOG_API_KEY` project

### 5. Set OpenAI spend limit on staging key

In OpenAI dashboard → Usage limits → set monthly cap (e.g. $20). This is the ONLY place where staging cost control lives — model stays identical to prod.

### 6. Install systemd units

```bash
sudo cp ops/systemd/*.service ops/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Prod (always-on)
sudo systemctl enable --now skibot-prod.service
sudo systemctl enable --now skibot-cleanup-prod.timer
sudo systemctl enable --now skibot-backup-prod.timer   # if you have a timer file for this

# Staging (on-demand — DO NOT enable, just leave the unit installed)
# Start manually when you want to test:
sudo systemctl start skibot-staging.service
# Stop when done:
sudo systemctl stop skibot-staging.service
```

### 7. Stop the old `skibot.service` (if it's still running)

```bash
sudo systemctl disable --now skibot.service
```

Verify nothing else listens on the prod bot token:
```bash
sudo systemctl status skibot-prod.service
journalctl -u skibot-prod.service -n 50
```

## On-demand staging workflow

```bash
# Start
sudo systemctl start skibot-staging.service

# Watch logs
journalctl -u skibot-staging.service -f

# Stop
sudo systemctl stop skibot-staging.service
```

When stopped:
- no Telegram long-poll → zero OpenAI spend
- Redis DB 1 keys persist until TTL (24h), but don't affect prod (DB 0)
- PG staging database is left intact

## Isolation matrix — what prevents cross-env contamination

| Resource | Prod | Staging | Isolation mechanism |
|----------|------|---------|---------------------|
| Bot token | `@alpineski_bot` | `@alpineski_staging_bot` | @BotFather (different token) |
| PG | `skibot` | `skibot_staging` | different database name |
| Redis | `db=0` | `db=1` | `REDIS_DB` env var |
| PDFs | `/home/ubuntu/ski-mvp-bot/report_*.pdf` | `/var/lib/skibot/staging/report_*.pdf` | `OUTPUT_DIR` |
| Video tmp | `/tmp/skibot_*.mp4` | `/tmp/skibot_staging/skibot_*.mp4` | `TMP_DIR` |
| Logs | `/home/ubuntu/ski-mvp-bot/logs` | `/var/lib/skibot/staging/logs` | `LOG_DIR` |
| Backups | `/home/ubuntu/backups/skibot_*` | (no backups for staging by default) | `BACKUP_DIR`+`BACKUP_PREFIX` |
| PostHog | same key, event prop `env=prod` | same key, event prop `env=staging` | tag in `posthog_client` |
| OpenAI | prod key | staging key (hard-cap) | `OPENAI_API_KEY` |
| systemd | `skibot-prod.service` (MemoryMax=1500M) | `skibot-staging.service` (MemoryMax=800M, CPUQuota=100%) | cgroups |

## Known residual risks (manual vigilance required)

1. **OWNER_ID fallback** — if you forget `OWNER_ID` in `.env.staging`, config falls back to `ALLOWED_USER_IDS[0]` = `202921941`. Admin reports from staging would arrive in your normal chat. Always set it explicitly.
2. **`init_db()` seed** — both envs seed `202921941` and `201955370` into `access_list`. Harmless unless you want a clean-slate staging DB.
3. **Same Playwright/venv** — both services share `/home/ubuntu/ski-mvp-bot/venv`. A `pip install` for staging experiment will affect prod. If you test dependency changes, use a separate venv.
4. **PostHog single project** — `env=staging` tag filters dashboards but a misconfigured filter would show staging data. Separate PostHog project is strictly safer.
5. **Schema drift** — `init_db` is idempotent for `CREATE TABLE IF NOT EXISTS`. If you introduce destructive ALTERs, test on staging first; there is no migration framework yet.
