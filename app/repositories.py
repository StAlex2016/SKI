from app.db import get_connection


# ── USERS ──────────────────────────────────────────────────────────────────────

def save_user(telegram_user_id, username=None, first_name=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (telegram_user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_user_id) DO NOTHING
    """, (telegram_user_id, username, first_name))
    conn.commit()
    cur.close()
    conn.close()


def update_athlete_profile(telegram_user_id, name=None, birth_year=None, category=None):
    conn = get_connection()
    cur = conn.cursor()
    if name is not None:
        cur.execute("""
            UPDATE users SET athlete_name = %s WHERE telegram_user_id = %s
        """, (name, telegram_user_id))
    if birth_year is not None:
        cur.execute("""
            UPDATE users SET birth_year = %s WHERE telegram_user_id = %s
        """, (birth_year, telegram_user_id))
    if category is not None:
        cur.execute("""
            UPDATE users SET category = %s WHERE telegram_user_id = %s
        """, (category, telegram_user_id))
    conn.commit()
    cur.close()
    conn.close()


def get_user_profile(telegram_user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT athlete_name, birth_year, category
        FROM users WHERE telegram_user_id = %s
    """, (telegram_user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {
        "athlete_name": row["athlete_name"],
        "birth_year": row["birth_year"],
        "category": row["category"],
    }


def get_analysis_count(telegram_user_id) -> int:
    """Количество успешных анализов пользователя — для лимита бесплатных."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) as cnt FROM analysis
        WHERE telegram_user_id = %s AND status = 'success'
    """, (telegram_user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] if row else 0


# ── ANALYSIS ───────────────────────────────────────────────────────────────────

def save_analysis(telegram_user_id, photos_count, result_text, status,
                   score=None, mode=None, discipline=None, lang=None, run_date=None) -> int:
    # Truncate result_text to 500 chars — enough for debug, avoids DB bloat
    if result_text and len(result_text) > 500:
        result_text = result_text[:500]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO analysis (telegram_user_id, photos_count, result_text, status,
                              score, mode, discipline, lang, run_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (telegram_user_id, photos_count, result_text, status,
           score, mode, discipline, lang, run_date))
    analysis_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return analysis_id


# ── FEEDBACK ───────────────────────────────────────────────────────────────────

def save_feedback(telegram_user_id, analysis_id, feedback_type, comment=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO feedback (telegram_user_id, analysis_id, feedback_type, comment)
        VALUES (%s, %s, %s, %s)
    """, (telegram_user_id, analysis_id, feedback_type, comment))
    conn.commit()
    cur.close()
    conn.close()


# ── STATS ──────────────────────────────────────────────────────────────────────

def get_stats():
    """Для /stats команды — общая аналитика бота."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM users")
    users = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) as cnt FROM analysis WHERE status = 'success'")
    analyses = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT feedback_type, COUNT(*) as cnt
        FROM feedback GROUP BY feedback_type
    """)
    feedback = {row["feedback_type"]: row["cnt"] for row in cur.fetchall()}

    cur.execute("""
        SELECT category, COUNT(*) as cnt
        FROM users WHERE category IS NOT NULL
        GROUP BY category ORDER BY cnt DESC
    """)
    categories = [(row["category"], row["cnt"]) for row in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "users": users,
        "analyses": analyses,
        "feedback": feedback,
        "categories": categories,
    }


# ── EVENT TRACKING ─────────────────────────────────────────────────────────────

import json as _json

def track(telegram_user_id, event_type: str, **payload):
    """Append a user/system event to the events table.

    Usage:
      track(user_id, "start", lang="ru")
      track(user_id, "discipline_selected", discipline="GS")
      track(user_id, "analysis_completed", mode="photo", duration_sec=12.4, score=7.5)
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO events (telegram_user_id, event_type, payload)
            VALUES (%s, %s, %s)
        """, (telegram_user_id, event_type, _json.dumps(payload, ensure_ascii=False, default=str)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        # Tracking must never crash the bot — swallow errors silently
        pass


# ── STATS AGGREGATORS ──────────────────────────────────────────────────────────

def get_stats_window(days: int = 1) -> dict:
    """Aggregate stats for the last N days."""
    conn = get_connection()
    cur = conn.cursor()

    # New users in window
    cur.execute("""
        SELECT COUNT(*) AS cnt FROM users
        WHERE created_at >= NOW() - INTERVAL '%s days'
    """, (days,))
    new_users = cur.fetchone()["cnt"]

    # Analyses by status
    cur.execute("""
        SELECT status, COUNT(*) AS cnt FROM analysis
        WHERE created_at >= NOW() - INTERVAL '%s days'
        GROUP BY status
    """, (days,))
    status_rows = cur.fetchall()
    status_counts = {r["status"]: r["cnt"] for r in status_rows}

    # Feedback counts
    cur.execute("""
        SELECT feedback_type, COUNT(*) AS cnt FROM feedback
        WHERE created_at >= NOW() - INTERVAL '%s days'
        GROUP BY feedback_type
    """, (days,))
    fb_counts = {r["feedback_type"]: r["cnt"] for r in cur.fetchall()}

    # Active users (DAU / WAU)
    cur.execute("""
        SELECT COUNT(DISTINCT telegram_user_id) AS cnt FROM events
        WHERE created_at >= NOW() - INTERVAL '%s days'
    """, (days,))
    active_row = cur.fetchone()
    active_users = active_row["cnt"] if active_row else 0

    # Events by type (for funnel)
    cur.execute("""
        SELECT event_type, COUNT(*) AS cnt FROM events
        WHERE created_at >= NOW() - INTERVAL '%s days'
        GROUP BY event_type
        ORDER BY cnt DESC
    """, (days,))
    event_counts = {r["event_type"]: r["cnt"] for r in cur.fetchall()}

    # Mode / discipline / run_type breakdowns (from events.payload)
    cur.execute("""
        SELECT payload->>'discipline' AS k, COUNT(*) AS cnt FROM events
        WHERE event_type = 'analysis_started'
          AND created_at >= NOW() - INTERVAL '%s days'
          AND payload->>'discipline' IS NOT NULL
        GROUP BY k ORDER BY cnt DESC
    """, (days,))
    by_discipline = {r["k"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("""
        SELECT payload->>'mode' AS k, COUNT(*) AS cnt FROM events
        WHERE event_type = 'analysis_started'
          AND created_at >= NOW() - INTERVAL '%s days'
          AND payload->>'mode' IS NOT NULL
        GROUP BY k ORDER BY cnt DESC
    """, (days,))
    by_mode = {r["k"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("""
        SELECT payload->>'lang' AS k, COUNT(*) AS cnt FROM events
        WHERE event_type = 'start'
          AND created_at >= NOW() - INTERVAL '%s days'
          AND payload->>'lang' IS NOT NULL
        GROUP BY k ORDER BY cnt DESC
    """, (days,))
    by_lang = {r["k"]: r["cnt"] for r in cur.fetchall()}

    # Recent errors
    cur.execute("""
        SELECT payload->>'where' AS place, payload->>'message' AS msg,
               created_at, telegram_user_id
        FROM events
        WHERE event_type = 'error'
          AND created_at >= NOW() - INTERVAL '%s days'
        ORDER BY created_at DESC LIMIT 10
    """, (days,))
    recent_errors = [dict(r) for r in cur.fetchall()]

    # OpenAI cost breakdown
    cur.execute("""
        SELECT payload->>'model' AS model,
               COUNT(*) AS calls,
               SUM((payload->>'prompt_tokens')::int) AS prompt_tokens,
               SUM((payload->>'completion_tokens')::int) AS completion_tokens,
               SUM((payload->>'cost_usd')::float) AS cost_usd,
               AVG((payload->>'latency_sec')::float) AS avg_latency
        FROM events
        WHERE event_type = 'openai_call'
          AND created_at >= NOW() - INTERVAL '%s days'
        GROUP BY model
        ORDER BY cost_usd DESC NULLS LAST
    """, (days,))
    openai_breakdown = [dict(r) for r in cur.fetchall()]
    total_cost = sum((r['cost_usd'] or 0) for r in openai_breakdown)

    cur.close()
    conn.close()

    return {
        "window_days": days,
        "new_users": new_users,
        "active_users": active_users,
        "analyses": status_counts,
        "feedback": fb_counts,
        "events_total": event_counts,
        "by_discipline": by_discipline,
        "by_mode": by_mode,
        "by_lang": by_lang,
        "recent_errors": recent_errors,
        "openai_breakdown": openai_breakdown,
        "openai_cost_total": round(total_cost, 4),
    }


def get_funnel(days: int = 1) -> dict:
    """Conversion funnel: start -> discipline -> upload -> analysis -> feedback."""
    conn = get_connection()
    cur = conn.cursor()

    stages = [
        ("started", "start"),
        ("mode_chosen", "mode_selected"),
        ("discipline_chosen", "discipline_selected"),
        ("uploaded", "photo_uploaded"),
        ("analysis_started", "analysis_started"),
        ("analysis_completed", "analysis_completed"),
        ("feedback_given", "feedback"),
    ]
    result = {}
    for label, etype in stages:
        cur.execute("""
            SELECT COUNT(DISTINCT telegram_user_id) AS cnt FROM events
            WHERE event_type = %s
              AND created_at >= NOW() - INTERVAL %s
        """, (etype, f"{days} days"))
        row = cur.fetchone()
        result[label] = row["cnt"] if row else 0

    # Also count video uploads
    cur.execute("""
        SELECT COUNT(DISTINCT telegram_user_id) AS cnt FROM events
        WHERE event_type = 'video_uploaded'
          AND created_at >= NOW() - INTERVAL %s
    """, (f"{days} days",))
    result["video_uploaded"] = cur.fetchone()["cnt"] or 0

    cur.close()
    conn.close()
    return result



def get_retention_metrics() -> dict:
    """DAU / WAU / MAU + returning users count."""
    conn = get_connection()
    cur = conn.cursor()

    def active_in(days):
        cur.execute("""
            SELECT COUNT(DISTINCT telegram_user_id) AS cnt FROM events
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        return cur.fetchone()["cnt"] or 0

    dau = active_in(1)
    wau = active_in(7)
    mau = active_in(30)

    # Returning: users active in last 7 days AND active in the prior 7 (day-7 to day-14)
    cur.execute("""
        SELECT COUNT(DISTINCT a.telegram_user_id) AS cnt
        FROM events a
        WHERE a.created_at >= NOW() - INTERVAL '7 days'
          AND EXISTS (
            SELECT 1 FROM events b
            WHERE b.telegram_user_id = a.telegram_user_id
              AND b.created_at >= NOW() - INTERVAL '14 days'
              AND b.created_at <  NOW() - INTERVAL '7 days'
          )
    """)
    returning_7d = cur.fetchone()["cnt"] or 0

    cur.close()
    conn.close()
    return {
        "DAU": dau,
        "WAU": wau,
        "MAU": mau,
        "returning_7d": returning_7d,
    }


def get_top_users(days: int = 7, limit: int = 5) -> list:
    """Top users by event count in window."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.telegram_user_id, COUNT(*) AS events,
               COUNT(*) FILTER (WHERE e.event_type = 'analysis_completed') AS analyses,
               u.username, u.athlete_name
        FROM events e
        LEFT JOIN users u ON u.telegram_user_id = e.telegram_user_id
        WHERE e.created_at >= NOW() - INTERVAL '%s days'
          AND e.telegram_user_id IS NOT NULL
        GROUP BY e.telegram_user_id, u.username, u.athlete_name
        ORDER BY events DESC
        LIMIT %s
    """, (days, limit))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_user_timeline(telegram_user_id: int, limit: int = 30) -> list:
    """Recent events for a specific user."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT event_type, payload, created_at
        FROM events
        WHERE telegram_user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (telegram_user_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_recent_errors(limit: int = 10) -> list:
    """Recent errors across all users."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT telegram_user_id,
               payload->>'where' AS place,
               payload->>'message' AS msg,
               created_at
        FROM events
        WHERE event_type = 'error'
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows



# ── ACCESS LIST (approval-based whitelist) ─────────────────────────────────────

import time as _access_time

# Tiny in-memory cache for is_approved checks — invalidated on approve/deny.
_ACCESS_CACHE: dict = {}
_ACCESS_CACHE_TTL = 60  # seconds


def is_approved(telegram_user_id: int) -> bool:
    """True if user is in access_list with status='approved'. Cached 60s."""
    now = _access_time.time()
    cached = _ACCESS_CACHE.get(telegram_user_id)
    if cached is not None and now - cached[1] < _ACCESS_CACHE_TTL:
        return cached[0]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM access_list WHERE telegram_user_id = %s AND status = %s LIMIT 1",
        (telegram_user_id, "approved"),
    )
    ok = cur.fetchone() is not None
    cur.close()
    conn.close()
    _ACCESS_CACHE[telegram_user_id] = (ok, now)
    return ok


def request_access(telegram_user_id: int, username: str = None, first_name: str = None) -> dict:
    """Record new access request. Returns dict {status, is_new_request}.
    - is_new_request=True if we just inserted a pending row
    - status='pending'|'approved'|'denied' current record status
    """
    conn = get_connection()
    cur = conn.cursor()
    # Insert as pending if absent
    cur.execute("""
        INSERT INTO access_list (telegram_user_id, username, first_name, status)
        VALUES (%s, %s, %s, 'pending')
        ON CONFLICT (telegram_user_id) DO NOTHING
        RETURNING telegram_user_id
    """, (telegram_user_id, username, first_name))
    inserted = cur.fetchone() is not None
    # Fetch current row
    cur.execute(
        "SELECT status FROM access_list WHERE telegram_user_id = %s",
        (telegram_user_id,),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {
        "is_new_request": inserted,
        "status": row["status"] if row else "pending",
    }


def approve_user(telegram_user_id: int, approved_by: int = None) -> bool:
    """Mark user as approved. Returns True if status actually changed."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE access_list
        SET status = 'approved', decided_at = NOW(), decided_by = %s
        WHERE telegram_user_id = %s AND status != 'approved'
        RETURNING telegram_user_id
    """, (approved_by, telegram_user_id))
    changed = cur.fetchone() is not None
    # If user not in table yet, insert directly as approved
    if not changed:
        cur.execute("""
            INSERT INTO access_list (telegram_user_id, status, decided_at, decided_by)
            VALUES (%s, 'approved', NOW(), %s)
            ON CONFLICT (telegram_user_id) DO NOTHING
            RETURNING telegram_user_id
        """, (telegram_user_id, approved_by))
        changed = cur.fetchone() is not None
    conn.commit()
    cur.close()
    conn.close()
    _ACCESS_CACHE.pop(telegram_user_id, None)
    return changed


def deny_user(telegram_user_id: int, denied_by: int = None) -> bool:
    """Mark user as denied. Returns True if status actually changed."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE access_list
        SET status = 'denied', decided_at = NOW(), decided_by = %s
        WHERE telegram_user_id = %s AND status != 'denied'
        RETURNING telegram_user_id
    """, (denied_by, telegram_user_id))
    changed = cur.fetchone() is not None
    if not changed:
        cur.execute("""
            INSERT INTO access_list (telegram_user_id, status, decided_at, decided_by)
            VALUES (%s, 'denied', NOW(), %s)
            ON CONFLICT (telegram_user_id) DO NOTHING
            RETURNING telegram_user_id
        """, (telegram_user_id, denied_by))
        changed = cur.fetchone() is not None
    conn.commit()
    cur.close()
    conn.close()
    _ACCESS_CACHE.pop(telegram_user_id, None)
    return changed


def list_pending() -> list:
    """Return list of pending access requests."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT telegram_user_id, username, first_name, requested_at
        FROM access_list
        WHERE status = 'pending'
        ORDER BY requested_at ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def list_all_access() -> list:
    """Return all access records for admin review."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT telegram_user_id, username, first_name, status, requested_at, decided_at
        FROM access_list
        ORDER BY
          CASE status WHEN \'pending\' THEN 0 WHEN \'approved\' THEN 1 ELSE 2 END,
          requested_at DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows
