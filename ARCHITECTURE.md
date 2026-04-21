# Alpine Ski Performance Lab — Architecture

Визуальная схема проекта. Mermaid-диаграммы рендерятся прямо в GitHub.

---

## 1. High-level architecture

Кто с кем разговаривает: юзер → Telegram → VPS → внешние API → хранилища.

```mermaid
flowchart TB
    %% ─── USERS ───
    subgraph Users["👥 Users"]
        direction LR
        U["🎿 Skier / Parent / Coach"]
        A["👤 Owner / Admin<br/>(202921941)"]
    end

    %% ─── TELEGRAM ───
    subgraph TG["📱 Telegram Platform"]
        BOT["@alpineski_bot<br/>(Bot API)"]
    end

    %% ─── EXTERNAL ───
    subgraph Ext["☁️ External APIs"]
        direction LR
        OAI["🧠 OpenAI<br/>GPT-4.1 · GPT-4o-mini"]
        PH["📊 PostHog<br/>eu.posthog.com"]
        GH["📝 GitHub<br/>StAlex2016/SKI"]
    end

    %% ─── VPS ───
    subgraph VPS["🖥️ OVH VPS · Ubuntu · Python 3.13"]
        direction TB

        subgraph Systemd["⚙️ systemd units"]
            SV["skibot.service<br/>(main bot)"]
            CT["skibot-cleanup.timer<br/>(hourly PDFs)"]
            BT["skibot-backup.timer<br/>(nightly pg_dump)"]
        end

        subgraph App["🐍 Python app"]
            direction TB
            MAIN["main.py<br/>handlers · callbacks · admin"]
            SVC["services/<br/>openai · video · pdf"]
            UT["utils/<br/>formatter · parser · tracking"]
            ST["state.py<br/>(Redis wrapper)"]
            REP["repositories.py<br/>(Postgres CRUD)"]
        end

        subgraph Tools["🛠️ System tools"]
            direction LR
            FF["ffmpeg /<br/>ffprobe"]
            PW["Playwright<br/>Chromium"]
        end

        subgraph Storage["💾 Storage"]
            direction LR
            PG[("🐘 PostgreSQL 17<br/>users · analysis · feedback<br/>events · access_list")]
            RD[("⚡ Redis 7<br/>session state<br/>24h TTL")]
            FSL["📁 logs/<br/>bot.log (100MB rot.)"]
            FSB["📁 backups/<br/>14-day retention"]
        end
    end

    %% ─── CONNECTIONS ───
    U -->|"messages<br/>+media"| BOT
    A -->|"/admin<br/>/user id"| BOT
    BOT <-->|"long polling"| SV
    SV --> MAIN
    MAIN --> SVC
    MAIN --> UT
    MAIN --> ST
    MAIN --> REP

    SVC -->|"chat.completions"| OAI
    SVC --> FF
    SVC --> PW

    REP <-->|"SQL"| PG
    ST <-->|"get/set/TTL"| RD
    SV --> FSL

    UT -.->|"events mirror"| PH

    CT -.->|"cleanup orphans"| VPS
    BT -->|"pg_dump -gzip"| FSB

    App -.->|"git push"| GH

    classDef user fill:#e3f2fd,stroke:#1976d2,stroke-width:2px,color:#000
    classDef ext fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#000
    classDef tg fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#000
    classDef vps fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#000
    classDef storage fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#000
    class U,A user
    class OAI,PH,GH ext
    class BOT tg
    class SV,CT,BT,MAIN,SVC,UT,ST,REP,FF,PW vps
    class PG,RD,FSL,FSB storage
```

---

## 2. Data model (PostgreSQL)

```mermaid
erDiagram
    users ||--o{ analysis : has
    analysis ||--o{ feedback : gets
    users ||--o{ events : generates
    users ||--|| access_list : has

    users {
        bigint telegram_user_id PK
        text username
        text first_name
        text athlete_name
        int birth_year
        text category "U8 | U10 | U12 | U14 | U16 | U18 | Adult"
        timestamptz created_at
    }

    analysis {
        serial id PK
        bigint telegram_user_id FK
        int photos_count
        text result_text "first 500 chars"
        text status "success | error"
        float score
        text mode "photo | video"
        text discipline "SL | GS"
        text lang "ru | en"
        date run_date
        timestamptz created_at
    }

    feedback {
        serial id PK
        bigint telegram_user_id FK
        int analysis_id FK
        text feedback_type "positive | negative"
        text comment
        timestamptz created_at
    }

    events {
        serial id PK
        bigint telegram_user_id
        text event_type "start | analysis_completed | openai_call | ..."
        jsonb payload
        timestamptz created_at
    }

    access_list {
        bigint telegram_user_id PK
        text username
        text first_name
        text status "pending | approved | denied"
        timestamptz requested_at
        timestamptz decided_at
        bigint decided_by
    }
```

---

## 3. User flow (state machine)

От первого контакта до feedback.

```mermaid
stateDiagram-v2
    [*] --> AccessCheck : "/start"

    AccessCheck --> Pending : "not approved"
    AccessCheck --> ModeSelect : "approved"

    Pending --> ModeSelect : "admin pressed ✅ Allow"
    Pending --> [*] : "admin pressed ❌ Deny"

    ModeSelect --> AskName : "⚡ Quick (photo)"
    ModeSelect --> AskName : "🔍 Detailed (video)"

    AskName --> AskYear : "name"
    AskYear --> AskDiscipline : "year → category"
    AskDiscipline --> AskRunType : "SL | GS"

    state Branch <<choice>>
    AskRunType --> Branch : "training | race"

    Branch --> UploadPhotos : "photo mode"
    Branch --> UploadVideo : "video mode"

    UploadPhotos --> QualityCheck : "≥3 photos + analyze"
    QualityCheck --> PhotoAnalysis : "quality OK"
    QualityCheck --> UploadPhotos : "need more / reject"

    UploadVideo --> DateConfirm : "video saved"
    DateConfirm --> ExtraPhotos : "optional extras"
    ExtraPhotos --> VideoAnalysis : "analyze now"
    DateConfirm --> VideoAnalysis : "analyze now"

    PhotoAnalysis --> ShowPDF : "GPT + PDF gen"
    VideoAnalysis --> ShowPDF : "GPT + parse + PDF gen"

    ShowPDF --> Feedback : "👍 / 👎"
    Feedback --> [*]
```

---

## 4. Event types tracked

Вся наблюдаемость строится на таблице `events` (+ зеркалится в PostHog).

| Event | When fired | Key payload |
|-------|-----------|-------------|
| `start` | /start | `lang`, `username` |
| `access_requested` | new user hits bot | `username`, `first_name` |
| `user_approved` / `user_denied` | admin action | `target`, `via` |
| `mode_selected` | [Quick] / [Detailed] click | `mode` |
| `discipline_selected` | SL / GS | `discipline` |
| `run_type_selected` | training / race | `run_type` |
| `photo_uploaded` | per photo | `total`, `approved`, `new` |
| `video_uploaded` | per video | `size_bytes` |
| `run_date_detected` | ffprobe result | `run_date`, `source` |
| `run_date_changed` | user picked date | `run_date`, `source`, `days_ago` |
| `analysis_started` | GPT call begins | `mode`, `discipline`, `run_type` |
| `analysis_completed` | PDF sent | `mode`, `duration_sec`, `score` |
| `openai_call` | every GPT call | `model`, `tokens`, `cost_usd`, `latency_sec`, `purpose` |
| `feedback` | 👍 / 👎 | `type`, `analysis_id` |
| `error` | exception in handler | `where`, `message` |

---

## 5. Schedule / automation

```mermaid
gantt
    title Daily cadence (all MSK)
    dateFormat HH:mm
    axisFormat %H:%M

    section Bot
    Running (long-polling)    :active, 00:00, 24h

    section Automated reports
    Daily report → Owner      :milestone, 09:00, 0h
    Weekly report (Mon) → Owner :milestone, 09:05, 0h

    section Maintenance
    Hourly PDF cleanup        :10:00, 1h
    Hourly PDF cleanup        :11:00, 1h
    Hourly PDF cleanup        :12:00, 1h
    Nightly pg_dump           :03:00, 5m
```

---

## 6. Quick facts

- **Language:** Python 3.13
- **Framework:** python-telegram-bot 21.10
- **LLM:** OpenAI GPT-4.1 (video) · GPT-4.1-mini (frame selection) · GPT-4o-mini (photo)
- **PDF render:** Playwright + Chromium (headless)
- **Video frames:** ffmpeg @ 3 fps
- **Session store:** Redis 7 (24h TTL)
- **DB:** PostgreSQL 17 (5 tables)
- **Analytics:** self-hosted via `events` table + mirror to PostHog
- **Backups:** nightly pg_dump, 14-day retention
- **Logs:** RotatingFileHandler 10MB × 10 = 100MB
- **CI/CD:** none yet (manual `git push` + `systemctl restart`)
- **Hosting:** OVH VPS, Ubuntu
- **Owner access:** `/admin` inline panel + slash commands `/stats /user /allow /deny /pending`

---

## 7. What's not here yet

- 🚧 Landing page (domain + static site)
- 🚧 Payment processor (Telegram Stars / ЮKassa)
- 🚧 History / progress tracking in bot UI
- 🚧 Multi-athlete profiles (1 user = 1 athlete for now)
- 🚧 Subscription tiers (pay-per-use + season + annual)
- 🚧 Referral program
- 🚧 Tests, CI/CD, staging environment

See [product roadmap in conversation] for priority.
