import sqlite3
from pathlib import Path

DB_PATH = Path("big_brother.db")

MIGRATION_SQL = """
-- Pool per week (single group for now)
CREATE TABLE IF NOT EXISTS pools (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start_utc_date  TEXT    NOT NULL,   -- e.g. '2025-11-10'
    pool_points          REAL    NOT NULL DEFAULT 0.0,
    UNIQUE (week_start_utc_date)
);

-- Per-week stats for "you" (single user for now)
CREATE TABLE IF NOT EXISTS weekly_stats (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start_utc_date  TEXT    NOT NULL,
    productive_seconds   REAL    NOT NULL DEFAULT 0,
    other_seconds        REAL    NOT NULL DEFAULT 0,
    shopping_seconds     REAL    NOT NULL DEFAULT 0,
    gaming_seconds       REAL    NOT NULL DEFAULT 0,
    doomscroll_seconds   REAL    NOT NULL DEFAULT 0,
    jackingoff_seconds   REAL    NOT NULL DEFAULT 0,
    pool_contrib_points  REAL    NOT NULL DEFAULT 0,
    UNIQUE (week_start_utc_date)
);

-- Individual contribution events (one row per bad sample)
CREATE TABLE IF NOT EXISTS contribution_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id       INTEGER NOT NULL,
    ts_utc          TEXT    NOT NULL,
    label           INTEGER NOT NULL,
    duration_sec    REAL    NOT NULL,
    points          REAL    NOT NULL,
    FOREIGN KEY (sample_id) REFERENCES samples(id)
);

-- Simple key/value meta store so we know how far we've processed samples
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

def main():
    conn = sqlite3.connect(DB_PATH)
    with conn:
        conn.executescript(MIGRATION_SQL)
    conn.close()
    print("Pool/weekly_stats migration completed.")

if __name__ == "__main__":
    main()
