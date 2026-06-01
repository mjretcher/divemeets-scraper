"""
storage/db.py
Database manager for DiveMeets scraper.
Creates and manages SQLite databases split by org + year.
Schema: meets → events → results → divers → dive_sheets → team_standings
"""
import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager

DATA_DIR = Path(__file__).parent.parent / "data"


def db_path(org: str, year: int) -> Path:
    """Returns path like data/ncaa_2024.db or data/usa_2024.db"""
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{org.lower()}_{year}.db"


@contextmanager
def get_conn(org: str, year: int):
    """Context manager: opens connection, creates schema if needed, commits/closes."""
    path = db_path(org, year)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    -- ─────────────────────────────────────────
    -- MEETS
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS meets (
        meet_id         TEXT PRIMARY KEY,   -- DiveMeets internal meet ID
        name            TEXT NOT NULL,
        org             TEXT NOT NULL,      -- 'ncaa' | 'usa'
        year            INTEGER NOT NULL,
        start_date      TEXT,               -- ISO-8601
        end_date        TEXT,
        location        TEXT,
        city            TEXT,
        state           TEXT,
        host_team       TEXT,
        meet_url        TEXT,
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- EVENTS
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS events (
        event_id        TEXT PRIMARY KEY,   -- meet_id + '_' + event_num
        meet_id         TEXT NOT NULL REFERENCES meets(meet_id),
        event_num       INTEGER,
        name            TEXT NOT NULL,      -- e.g. "Women's 1M Springboard"
        gender          TEXT,               -- 'M' | 'F' | 'Mixed'
        board_height    TEXT,               -- '1m' | '3m' | 'platform' | '10m' etc.
        division        TEXT,               -- 'prelim' | 'semifinal' | 'final'
        num_dives       INTEGER,
        event_url       TEXT,
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- DIVERS
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS divers (
        diver_id        TEXT PRIMARY KEY,   -- DiveMeets profile ID
        first_name      TEXT,
        last_name       TEXT,
        full_name       TEXT NOT NULL,
        gender          TEXT,
        hometown        TEXT,
        home_state      TEXT,
        country         TEXT DEFAULT 'USA',
        team            TEXT,
        coach           TEXT,
        profile_url     TEXT,
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- RESULTS (per diver per event)
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS results (
        result_id       TEXT PRIMARY KEY,   -- event_id + '_' + diver_id
        event_id        TEXT NOT NULL REFERENCES events(event_id),
        meet_id         TEXT NOT NULL REFERENCES meets(meet_id),
        diver_id        TEXT NOT NULL REFERENCES divers(diver_id),
        team            TEXT,
        place           INTEGER,
        total_score     REAL,
        prelim_score    REAL,
        semifinal_score REAL,
        final_score     REAL,
        qualified       INTEGER DEFAULT 0,  -- boolean
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- DIVE SHEETS (individual dives within a result)
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS dive_sheets (
        dive_id         TEXT PRIMARY KEY,   -- result_id + '_' + round + '_' + dive_num
        result_id       TEXT NOT NULL REFERENCES results(result_id),
        event_id        TEXT NOT NULL,
        diver_id        TEXT NOT NULL,
        round           TEXT,               -- 'prelim' | 'semifinal' | 'final'
        dive_num        INTEGER,            -- order within the sheet (1-6 or 1-11)
        dive_code       TEXT,               -- e.g. '6245D'
        dive_name       TEXT,               -- e.g. 'Back 2.5 Somersaults 1 Twist'
        position        TEXT,               -- 'A' tuck | 'B' pike | 'C' straight | 'D' free
        dd              REAL,               -- degree of difficulty
        judge_scores    TEXT,               -- JSON array e.g. "[7.5, 8.0, 7.5, 8.5, 7.0]"
        net_score       REAL,               -- after dropping high/low
        total_points    REAL,               -- net_score * dd
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- TEAM STANDINGS
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS team_standings (
        standing_id     TEXT PRIMARY KEY,   -- meet_id + '_' + team_name
        meet_id         TEXT NOT NULL REFERENCES meets(meet_id),
        team_name       TEXT NOT NULL,
        place           INTEGER,
        total_points    REAL,
        scraped_at      TEXT NOT NULL
    );

    -- ─────────────────────────────────────────
    -- SCRAPE LOG (track what's been done)
    -- ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS scrape_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type     TEXT NOT NULL,      -- 'meet' | 'event' | 'diver' | 'divesheet' | 'standings'
        entity_id       TEXT NOT NULL,
        status          TEXT NOT NULL,      -- 'ok' | 'error' | 'skipped'
        message         TEXT,
        scraped_at      TEXT NOT NULL,
        UNIQUE(entity_type, entity_id)
    );

    -- ─────────────────────────────────────────
    -- INDEXES for analytics query performance
    -- ─────────────────────────────────────────
    CREATE INDEX IF NOT EXISTS idx_results_meet    ON results(meet_id);
    CREATE INDEX IF NOT EXISTS idx_results_diver   ON results(diver_id);
    CREATE INDEX IF NOT EXISTS idx_results_event   ON results(event_id);
    CREATE INDEX IF NOT EXISTS idx_events_meet     ON events(meet_id);
    CREATE INDEX IF NOT EXISTS idx_dive_sheets_result ON dive_sheets(result_id);
    CREATE INDEX IF NOT EXISTS idx_dive_sheets_diver  ON dive_sheets(diver_id);
    CREATE INDEX IF NOT EXISTS idx_standings_meet  ON team_standings(meet_id);
    """)


def already_scraped(conn: sqlite3.Connection, entity_type: str, entity_id: str) -> bool:
    """Check scrape_log to avoid re-scraping completed entities."""
    row = conn.execute(
        "SELECT status FROM scrape_log WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id)
    ).fetchone()
    return row is not None and row["status"] == "ok"


def log_scraped(conn: sqlite3.Connection, entity_type: str, entity_id: str,
                status: str = "ok", message: str = ""):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO scrape_log(entity_type, entity_id, status, message, scraped_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            status=excluded.status, message=excluded.message, scraped_at=excluded.scraped_at
    """, (entity_type, entity_id, status, message, now))
