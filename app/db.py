import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", 5432)),
        dbname=os.getenv("PG_DB", "skibot"),
        user=os.getenv("PG_USER", "skibot"),
        password=os.getenv("PG_PASSWORD", ""),
        cursor_factory=RealDictCursor,
    )


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                SERIAL PRIMARY KEY,
            telegram_user_id  BIGINT UNIQUE NOT NULL,
            username          TEXT,
            first_name        TEXT,
            athlete_name      TEXT,
            birth_year        INTEGER,
            category          TEXT,
            created_at        TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analysis (
            id               SERIAL PRIMARY KEY,
            telegram_user_id BIGINT NOT NULL,
            photos_count     INTEGER,
            result_text      TEXT,
            status           TEXT,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id               SERIAL PRIMARY KEY,
            telegram_user_id BIGINT NOT NULL,
            analysis_id      INTEGER REFERENCES analysis(id),
            feedback_type    TEXT,
            comment          TEXT,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id               SERIAL PRIMARY KEY,
            telegram_user_id BIGINT,
            event_type       TEXT NOT NULL,
            payload          JSONB,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(telegram_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC)")

    # Access list — whitelist management with approval flow
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_list (
            telegram_user_id BIGINT PRIMARY KEY,
            username         TEXT,
            first_name       TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            requested_at     TIMESTAMPTZ DEFAULT NOW(),
            decided_at       TIMESTAMPTZ,
            decided_by       BIGINT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_access_status ON access_list(status)")

    # Ensure run_date column exists on analysis table (idempotent)
    cur.execute("ALTER TABLE analysis ADD COLUMN IF NOT EXISTS run_date DATE")

    # Seed initial whitelist (idempotent)
    for seed_uid in (202921941, 201955370):
        cur.execute("""
            INSERT INTO access_list (telegram_user_id, status, decided_at)
            VALUES (%s, 'approved', NOW())
            ON CONFLICT (telegram_user_id) DO UPDATE
              SET status = 'approved', decided_at = COALESCE(access_list.decided_at, NOW())
        """, (seed_uid,))

    conn.commit()
    cur.close()
    conn.close()
