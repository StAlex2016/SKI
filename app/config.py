import os
from dotenv import load_dotenv

load_dotenv()

# ── ENVIRONMENT ────────────────────────────────────────────────────────────────
APP_ENV = os.getenv("APP_ENV", "prod").strip().lower()  # "prod" | "staging"
IS_STAGING = APP_ENV == "staging"

# ── CORE ───────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── ACCESS ─────────────────────────────────────────────────────────────────────
def _parse_ids(csv: str) -> list[int]:
    out = []
    for s in (csv or "").split(","):
        s = s.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            pass
    return out

ALLOWED_USER_IDS = _parse_ids(os.getenv("ALLOWED_USER_IDS", "202921941,201955370"))
OWNER_ID = int(os.getenv("OWNER_ID", str(ALLOWED_USER_IDS[0] if ALLOWED_USER_IDS else 0)))

# ── FILESYSTEM (env-scoped; prod defaults preserve current behavior) ───────────
LOG_DIR    = os.getenv("LOG_DIR",    "logs")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")        # PDFs land in CWD by default
TMP_DIR    = os.getenv("TMP_DIR",    "/tmp")     # staging overrides to /tmp/skibot_staging

# Ensure dirs exist on import (cheap, idempotent)
for _d in (LOG_DIR, OUTPUT_DIR, TMP_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass

# ── REDIS ──────────────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB   = int(os.getenv("REDIS_DB", 0))       # 0=prod, 1=staging by convention
