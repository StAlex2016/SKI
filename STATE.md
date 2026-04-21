# Project State — Alpine Ski Performance Lab

> **Handoff document for continuing work in a new Claude Code session.**
> First message in new session: *"Прочитай STATE.md и продолжим с того места"* — этого достаточно для полного контекста.

---

## 1. Project identity

| Field | Value |
|-------|-------|
| Brand name | **Alpine Ski Performance Lab** |
| Telegram bot | [@alpineski_bot](https://t.me/alpineski_bot) |
| Bot ID | `8631079355` |
| Product | AI-powered alpine ski technique analysis (photos + video) |
| Output | Structured summary in Telegram chat + PDF report (A5 for photo, A4×3 for video) |
| Languages | RU + EN (auto-detect by Telegram `language_code`) |
| Disciplines | SL, GS · training / race · age U8 / U10 / U12 / U14 / U16 / U18 / Adult |
| Owner | Alexey, telegram_user_id=`202921941` |
| Second admin | `201955370` (lives in `ALLOWED_USERS` array) |

---

## 2. Infrastructure

### 2.1 Server (VPS)
- **Provider:** OVH
- **Host IP:** `51.75.19.214`
- **OS:** Ubuntu
- **Login:** `ubuntu` (via SSH key)
- **Working directory:** `/home/ubuntu/ski-mvp-bot/`
- **Python:** 3.13.3, venv at `/home/ubuntu/ski-mvp-bot/venv/`

### 2.2 systemd units
| Unit | Purpose | Schedule |
|------|---------|----------|
| `skibot.service` | Main bot (python -m app.main) | Always running, auto-restart |
| `skibot-cleanup.timer` → `skibot-cleanup.service` | Delete orphan `report_*.pdf` older than 60 min | Hourly |
| `skibot-backup.timer` → `skibot-backup.service` | `pg_dump` → `/home/ubuntu/backups/skibot_YYYY-MM-DD.sql.gz`, rotation 14 days | Nightly 00:00 UTC (03:00 MSK) |

### 2.3 System tools installed
- `ffmpeg` 7.1.1 + `ffprobe`
- `poppler-utils` (pdftoppm for testing)
- PostgreSQL 17.7 (local)
- Redis 7.0.15 (local, db=0)
- Playwright + Chromium (headless, for PDF)

### 2.4 Filesystem layout
```
/home/ubuntu/
├── ski-mvp-bot/          # git repo + app
│   ├── .env              # secrets (NOT in git, 600)
│   ├── .git/             # local git
│   ├── .gitignore
│   ├── ARCHITECTURE.md   # visual diagrams
│   ├── STATE.md          # this doc
│   ├── app/              # python source
│   ├── scripts/
│   │   └── backup_db.sh  # pg_dump + rotation script
│   ├── venv/             # not in git
│   ├── logs/bot.log      # rotates 10MB × 10 = 100MB
│   └── archive_20260420/ # old .bak files (can delete later)
└── backups/              # pg_dump tarballs, 600 perms
    └── skibot_YYYY-MM-DD.sql.gz
```

---

## 3. External services / integrations

| Service | Role | Credentials location |
|---------|------|----------------------|
| **Telegram Bot API** | Bot connectivity | `TELEGRAM_BOT_TOKEN` in `.env` |
| **OpenAI** | GPT-4.1 (video), GPT-4.1-mini (frame selection), GPT-4o-mini (photo) | `OPENAI_API_KEY` in `.env`, `OPENAI_MODEL=gpt-4o-mini` |
| **PostHog** | Event mirror + analytics | `POSTHOG_API_KEY=phc_m5g8a4p3...` · `POSTHOG_HOST=https://eu.posthog.com` in `.env` |
| **GitHub** | Code versioning | SSH deploy key `~/.ssh/id_ed25519_github` on server · Repo: https://github.com/StAlex2016/SKI · Owner: `StAlex2016` |
| **PostgreSQL** | Persistent store | `PG_HOST=localhost` · `PG_DB=skibot` · `PG_USER=skibot` · `PG_PASSWORD=...` in `.env` |
| **Redis** | Session state (24h TTL) | `REDIS_HOST=localhost` · `REDIS_PORT=6379` in `.env` |

### 3.1 `.env` variable inventory (for reference)
```
TELEGRAM_BOT_TOKEN=<secret>
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-4o-mini
PG_HOST=localhost
PG_PORT=5432
PG_DB=skibot
PG_USER=skibot
PG_PASSWORD=<secret>
REDIS_HOST=localhost
REDIS_PORT=6379
POSTHOG_API_KEY=phc_m5g8a4p3uGj4nbabnuFUgxZisYkGpsbvFQ65AsWkaTpX
POSTHOG_HOST=https://eu.posthog.com
OWNER_ID=202921941                   # optional; falls back to ALLOWED_USERS[0]
```

---

## 4. Project structure (code)

```
app/
├── __init__.py
├── config.py              # loads .env → BOT_TOKEN, OPENAI_API_KEY, OPENAI_MODEL
├── db.py                  # get_connection() + init_db() (creates all 5 tables)
├── main.py                # ~1800 lines — god file, all handlers + callbacks + admin
├── repositories.py        # CRUD: users, analysis, feedback, events, access_list
├── state.py               # Redis-backed session state (24h TTL)
├── deploy.sh              # initial server setup (historical)
│
├── services/
│   ├── openai_service.py        # analyze_images() + check_images_quality() [photo]
│   ├── video_service.py         # analyze_video() + extract_run_date() + select_best_frames() [video]
│   ├── video_quality.py         # ffprobe-based video pre-validation
│   ├── pdf_service.py           # Photo PDF (A5, 1 page, build_html + generate_pdf)
│   └── pdf_detailed_service.py  # Video PDF (A4, 3 pages, build_html_detailed + generate_pdf_detailed)
│
├── utils/
│   ├── formatter.py         # format_analysis() — Telegram summary from GPT output
│   ├── video_parser.py      # parse_video_analysis() — GPT text → structured dict
│   ├── openai_tracking.py   # log_openai_usage() — per-call token + cost tracking
│   ├── posthog_client.py    # capture() + identify() with graceful degradation
│   ├── logger.py            # RotatingFileHandler 10MB × 10
│   └── text_utils.py        # _clamp() text truncation

scripts/
└── backup_db.sh             # run by systemd timer

ARCHITECTURE.md              # visual diagrams (Mermaid)
STATE.md                     # this doc
```

### 4.1 Key functions / where to look
| Task | File | Function |
|------|------|----------|
| Add new bot command | `app/main.py` | Look at `stats_cmd`, `pending_cmd`, register in `main()` |
| Change photo analysis prompt | `app/services/openai_service.py` | Inside `analyze_images()` |
| Change video analysis prompt | `app/services/video_service.py` | `_PROMPTS` dict with 8 variants (training/race × GS/SL × ru/en) |
| Add new event type | Anywhere | Just call `track(user_id, "event_name", **props)` — auto-goes to DB + PostHog |
| Add new tracked field | `app/utils/openai_tracking.py` | `_PRICING` dict if new model |
| Modify photo PDF | `app/services/pdf_service.py` | `build_html()` + CSS inline |
| Modify video PDF | `app/services/pdf_detailed_service.py` | `build_html_detailed()` — stricter 750px/page budget |
| Session state ops | `app/state.py` | `get/set/delete` (generic), specific helpers below |
| Admin panel additions | `app/main.py` | `keyboard_admin_panel()` + callback handlers `admin_*` |

---

## 5. Database schema (PostgreSQL 17)

All tables auto-created by `init_db()` on bot startup (idempotent via `IF NOT EXISTS`).

### 5.1 Tables
```sql
-- 1. users — one row per telegram user
CREATE TABLE users (
    id                SERIAL PRIMARY KEY,
    telegram_user_id  BIGINT UNIQUE NOT NULL,
    username          TEXT,
    first_name        TEXT,
    athlete_name      TEXT,
    birth_year        INTEGER,
    category          TEXT,            -- U8/U10/U12/U14/U16/U18/Adult
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- 2. analysis — every photo/video analysis attempt (structured from 2026-04-20)
CREATE TABLE analysis (
    id               SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    photos_count     INTEGER,
    result_text      TEXT,             -- truncated to 500 chars since C2 fix
    status           TEXT,             -- 'success' | 'error'
    score            FLOAT,            -- overall score 0-10
    mode             TEXT,             -- 'photo' | 'video'
    discipline       TEXT,             -- 'SL' | 'GS'
    lang             TEXT,             -- 'ru' | 'en'
    run_date         DATE,             -- when the actual skiing happened (≠ created_at)
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- 3. feedback — 👍/👎 reactions + optional text comments
CREATE TABLE feedback (
    id               SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    analysis_id      INTEGER REFERENCES analysis(id),
    feedback_type    TEXT,             -- 'positive' | 'negative'
    comment          TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- 4. events — event log for funnel / observability (also mirrored to PostHog)
CREATE TABLE events (
    id               SERIAL PRIMARY KEY,
    telegram_user_id BIGINT,           -- NULL for system events
    event_type       TEXT NOT NULL,
    payload          JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_events_user ON events(telegram_user_id);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_created ON events(created_at DESC);

-- 5. access_list — approval-based whitelist (replaces hardcoded ALLOWED_USERS)
CREATE TABLE access_list (
    telegram_user_id BIGINT PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/denied
    requested_at     TIMESTAMPTZ DEFAULT NOW(),
    decided_at       TIMESTAMPTZ,
    decided_by       BIGINT
);
CREATE INDEX idx_access_status ON access_list(status);
```

### 5.2 Event type taxonomy
`start`, `access_requested`, `user_approved`, `user_denied`, `access_denied`, `mode_selected`, `discipline_selected`, `run_type_selected`, `photo_uploaded`, `video_uploaded`, `run_date_detected`, `run_date_changed`, `analysis_started`, `analysis_completed`, `openai_call`, `feedback`, `error`.

See `ARCHITECTURE.md` §4 for payload details.

---

## 6. Current feature state

| Feature | Status | Notes |
|---------|--------|-------|
| Photo analysis (3-5 photos → 1-page PDF) | ✅ live | GPT-4o-mini |
| Video analysis (≤60s → 3-page PDF) | ✅ live | GPT-4.1 · ffmpeg 3fps · 20 best frames |
| Run date auto-detect (ffprobe) + calendar picker | ✅ live | Since 2026-04-20 |
| Brand "Alpine Ski Performance Lab" in PDF + bot profile | ✅ live | Footer has clickable `@alpineski_bot` link |
| Approval flow (`/pending`, inline Allow/Deny) | ✅ live | Replaces hardcoded ALLOWED_USERS |
| Admin panel (`/admin` or slash menu) | ✅ live | Stats 24h/7d/30d, retention, errors, pending |
| Daily report 09:00 MSK | ✅ live | via JobQueue |
| Weekly report Mon 09:05 MSK | ✅ live | DAU/WAU/MAU + top 5 users |
| Error alerts to owner (`notify_owner`) | ✅ live | Stacktrace + user id |
| OpenAI cost tracking per call | ✅ live | `openai_call` event with tokens + USD |
| PostHog integration | ✅ live | All events mirrored |
| Feedback 👍/👎 + comment | ✅ live | Fixed after C2 round (analysis_id properly linked) |
| Log rotation 100 MB | ✅ live | 10 MB × 10 backups |
| Daily DB backups (14-day retention) | ✅ live | Gzipped pg_dump |
| Git + GitHub | ✅ live | Deploy key, auto-push |
| **Pricing / paywall** | ❌ NOT built | Decision needed |
| **Landing page** | ❌ NOT built | Needs domain + content |
| **History / progress UI** | ❌ NOT built | Big Phase 3 work |
| **Multi-athlete profiles** | ❌ NOT built | Currently 1 user = 1 athlete |
| **Payment processor** | ❌ NOT integrated | Telegram Stars or ЮKassa TBD |

---

## 7. Decisions made (chronological, summarised)

1. **Whitelist → approval flow** (DB table `access_list`, inline Allow/Deny). `ALLOWED_USERS` kept as seed + admin gate.
2. **Bot brand:** "Alpine Ski Performance Lab" (replaced "Ski Performance"). Username `@alpineski_bot` **not renamed** (would break existing links).
3. **PDF branding:** footer on photo + video PDF has clickable `t.me/alpineski_bot` (viral mechanic).
4. **Integrity rules in prompts:** no pseudo-precision (proc/sec), no contradictions strengths↔weaknesses, no age-group leaks. Qualitative labels only for race speed losses.
5. **Weakness markers:** 🟠 (key) / 🟡 (secondary) / no emoji (additional). Green 🟢 banned.
6. **Run date (video):** auto ffprobe extraction + presets (today/yesterday/3d/week/2w/month) + full calendar picker.
7. **Two dates in PDF:** Заезд (run_date) + Отчёт (report_date).
8. **Slash menu:** `/start` for all, `/admin` only for OWNER. Admin inline panel replaces multiple slash commands.
9. **Welcome text:** short + brand + tier hint (photo = quick / video = detailed). Removed "бесплатно" per user feedback.
10. **Bot description:** 🔒 PRO badge on video tier. "Первое знакомство" wording on photo tier.
11. **Observability:** events table in Postgres + PostHog mirror. Daily + weekly reports.
12. **Log retention:** 100 MB via RotatingFileHandler.
13. **DB backups:** nightly pg_dump with 14-day rotation.
14. **Version control:** Git + private GitHub repo + deploy key.
15. **Pricing model direction:** Free photo (one-time) → paid video (per-use) → Season Pass (to end of season) → Annual Pass (365 days). **Specific numbers NOT chosen yet.**

---

## 8. Open questions / next decisions

### 8.1 Pricing (blocks landing + paywall)
- [ ] **Choose price tier:** Variant A (consumer, 500/2990/4990₽) / B (premium, 990/4990/8990₽) / C (professional, 1500/7990/14990₽)?
- [ ] **Lifetime Pro for first 5-10 testers** — yes/no?
- [ ] **Payment processor** for MVP: Telegram Stars / ЮKassa / Stripe?
- [ ] **Photo free limit** — 1 or 3 analyses for lifetime?
- [ ] **Subscription per user or per athlete?** (decides multi-athlete architecture)

### 8.2 Landing page
- [ ] Domain name (suggestions: alpineskilab.com / alpineski.pro / .coach)
- [ ] Tech stack (Vercel+Next.js / plain HTML / Framer)
- [ ] Who writes copy (you / I draft / both)
- [ ] Real PDF screenshots for "Example report" block (get from testers)
- [ ] Hero image (skier on slope)

### 8.3 History / progress feature
- [ ] Store raw GPT output in `analysis.full_text` column (not yet added)?
- [ ] Store parsed dict in `analysis.parsed_data JSONB`?
- [ ] Regenerate PDF on-demand vs store in S3?
- [ ] `/history` command UI layout?
- [ ] Progress visualization: inline chart (matplotlib PNG) / ASCII / web?

### 8.4 User feedback round 1
- [ ] Send invite links to 3-5 testers (message templates ready)
- [ ] Watch real behaviour via `/user <id>` + daily reports
- [ ] After 1-2 days, compile findings + next polish round

---

## 9. Git history (last 10 commits, as of handoff)

```
e846ffd  docs: add ARCHITECTURE.md with Mermaid diagrams
db545f5  ux: sync EN welcome with RU (was left on old text)
09f2934  ux: cleaner welcome — remove free/premium hints
df5168c  ux: polish round 2 (quality msg cleanup, button order, warmer welcome)
b9aea6d  fix: UX polish — counter cleanup in video flow + friendly errors
583af51  feat: feedback fixes + PostHog integration
ccc8cbd  Initial commit: Alpine Ski Performance Lab bot
```

Repository: https://github.com/StAlex2016/SKI

---

## 10. Quick commands reference

```bash
# SSH to server
ssh ubuntu@51.75.19.214

# On server — quick status
systemctl is-active skibot skibot-cleanup.timer skibot-backup.timer
journalctl -u skibot -n 30 --no-pager

# Restart bot after code change
sudo systemctl restart skibot && sleep 3 && journalctl -u skibot -n 10

# Manual DB backup
/home/ubuntu/ski-mvp-bot/scripts/backup_db.sh

# Restore from a backup
gunzip -c /home/ubuntu/backups/skibot_YYYY-MM-DD.sql.gz | PGPASSWORD=$PG_PASSWORD psql -U skibot -d skibot

# Run audit script (live state)
cd /home/ubuntu/ski-mvp-bot && set -a && . .env && set +a && source venv/bin/activate
python3 /tmp/full_audit.py  # if you uploaded one

# Git workflow
cd /home/ubuntu/ski-mvp-bot
git status
git add <files>
git commit -m "..."
git push

# Verify PostHog events arriving
python3 -c "
from app.utils.posthog_client import capture, is_enabled
import posthog
print('enabled:', is_enabled())
capture(202921941, 'manual_ping', {'source': 'handoff'})
posthog.flush()
print('check eu.posthog.com')
"

# Send report to owner chat
python3 -c "
import asyncio
from telegram import Bot
from app.config import BOT_TOKEN
asyncio.run(Bot(token=BOT_TOKEN).send_message(202921941, 'test'))
"
```

---

## 11. Onboarding for new Claude Code session

### Minimum context to resume
1. Read `STATE.md` (this file) and `ARCHITECTURE.md` — 3 min
2. Check latest git log: `ssh ubuntu@51.75.19.214 "cd /home/ubuntu/ski-mvp-bot && git log -5 --oneline"`
3. Check bot is alive: `ssh ubuntu@51.75.19.214 "systemctl is-active skibot"`

### To continue any work
- **Code work** → edit files, commit, push, `systemctl restart skibot`. GitHub is source of truth.
- **DB inspection** → `ssh ...`, `set -a && . .env && set +a`, `psql -U skibot -d skibot`
- **Log inspection** → `journalctl -u skibot -n 100` or `tail -f logs/bot.log`
- **PostHog** → https://eu.posthog.com (account: Alexey's)
- **GitHub** → https://github.com/StAlex2016/SKI

### What I (Claude) can do autonomously
- Edit code locally via SSH → Python scripts that apply patches
- Deploy (restart systemd)
- Commit + push to GitHub
- Run DB queries
- Send messages to owner chat for verification
- Render + download PDFs for visual review

### What needs you (human)
- Answer product decisions (pricing, copy, ICP targeting)
- Register domain / GitHub org / payment accounts
- Upload assets (bot avatar via @BotFather, logo for landing)
- Provide `.env` additions (new API keys)
- Test features as real user

---

## 12. Known tech debt (not blocking, but noted)

- **main.py** is a god file (~1800 lines) — should be split: `handlers/` directory by flow
- **psycopg2** is synchronous — could be psycopg3 with async pool for better throughput
- **No tests** — zero pytest coverage, nothing stopping regressions
- **No CI/CD** — manual `git push` + `systemctl restart` per deploy
- **No staging env** — all changes go to production VPS directly
- **photo_msg_ids cleanup** assumes sequential flow — race conditions with multi-window users possible
- **_video_locks** dict grows per unique user — need periodic cleanup at scale
- **Rate limiting** missing — 1 user can spam 100 videos, DoS-able

Address if/when they start hurting. Not urgent at current scale (0 external users as of this doc).

---

## 13. Contact / location map

| Resource | Where |
|----------|-------|
| Server | `ssh ubuntu@51.75.19.214` |
| Code | https://github.com/StAlex2016/SKI |
| Bot | https://t.me/alpineski_bot |
| Analytics | https://eu.posthog.com |
| BotFather (avatar, username) | https://t.me/BotFather |
| Owner Telegram | `202921941` (Alexey) |
| Second admin | `201955370` |

---

*Last updated: this handoff. If working in a new session, add new commits / decisions to sections 7, 8, 9 as you go.*
