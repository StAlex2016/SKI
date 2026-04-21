"""
state.py — централизованное хранение состояния через Redis.
Заменяет все in-memory dict в main.py:
  user_photos, user_approved_photos, user_states,
  user_discipline, user_category, user_lang,
  user_last_analysis_id, user_photo_limit
"""
import os
import json
import redis

_r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=int(os.getenv("REDIS_DB", 0)),  # 0=prod, 1=staging — full isolation via Redis logical DB
    decode_responses=True,
)

TTL = 60 * 60 * 24  # 24 часа — сессия живёт сутки


def _key(user_id: int, field: str) -> str:
    return f"user:{user_id}:{field}"


# ── GENERIC ────────────────────────────────────────────────────────────────────

def get(user_id: int, field: str, default=None):
    val = _r.get(_key(user_id, field))
    if val is None:
        return default
    try:
        return json.loads(val)
    except Exception:
        return val


def set(user_id: int, field: str, value):
    _r.setex(_key(user_id, field), TTL, json.dumps(value))


def delete(user_id: int, field: str):
    _r.delete(_key(user_id, field))


# ── SHORTCUTS ──────────────────────────────────────────────────────────────────

def get_lang(user_id: int) -> str:
    return get(user_id, "lang", "ru")

def set_lang(user_id: int, lang: str):
    set(user_id, "lang", lang)


def get_state(user_id: int) -> str | None:
    return get(user_id, "state")

def set_state(user_id: int, state: str | None):
    set(user_id, "state", state)


def get_photos(user_id: int) -> list:
    return get(user_id, "photos", [])

def set_photos(user_id: int, photos: list):
    set(user_id, "photos", photos)

def append_photo(user_id: int, file_id: str):
    photos = get_photos(user_id)
    photos.append(file_id)
    set_photos(user_id, photos)


def get_approved(user_id: int) -> list:
    return get(user_id, "approved", [])

def set_approved(user_id: int, photos: list):
    set(user_id, "approved", photos)


def get_discipline(user_id: int) -> str:
    return get(user_id, "discipline", "GS")

def set_discipline(user_id: int, discipline: str):
    set(user_id, "discipline", discipline)


def get_category(user_id: int) -> str:
    return get(user_id, "category", "U12")

def set_category(user_id: int, category: str):
    set(user_id, "category", category)


def get_photo_limit(user_id: int) -> int:
    return get(user_id, "photo_limit", 5)

def set_photo_limit(user_id: int, limit: int):
    set(user_id, "photo_limit", limit)


def get_last_analysis_id(user_id: int) -> int | None:
    return get(user_id, "last_analysis_id")

def set_last_analysis_id(user_id: int, analysis_id: int):
    set(user_id, "last_analysis_id", analysis_id)


def get_analysis_mode(user_id: int) -> str:
    return get(user_id, "analysis_mode", "quick")

def set_analysis_mode(user_id: int, mode: str):
    set(user_id, "analysis_mode", mode)


def get_run_type(user_id: int) -> str:
    return get(user_id, "run_type", "training")

def set_run_type(user_id: int, run_type: str):
    set(user_id, "run_type", run_type)


def get_video_path(user_id: int) -> str | None:
    return get(user_id, "video_path")

def set_video_path(user_id: int, path: str):
    set(user_id, "video_path", path)


def reset_session(user_id: int):
    """Полный сброс сессии — при /start, restart, после анализа."""
    # NOTE: last_analysis_id is deliberately NOT reset — feedback arrives
    # AFTER reset_session (user clicks 👍/👎 after seeing PDF). We keep it
    # so save_feedback can link to the analysis. It's overwritten on next
    # analysis start, and has a 24h TTL from Redis.
    for field in ["photos", "approved", "state", "discipline",
                  "category", "photo_limit",
                  "analysis_mode", "run_type", "video_path", "photo_msg_ids"]:
        delete(user_id, field)
    set_photo_limit(user_id, 5)
