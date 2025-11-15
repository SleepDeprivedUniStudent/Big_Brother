import sqlite3
from pathlib import Path

DB_PATH = Path("big_brother.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT    NOT NULL,
    label        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    label          INTEGER NOT NULL,
    start_ts_utc   TEXT    NOT NULL,
    end_ts_utc     TEXT    NOT NULL,
    duration_sec   REAL    NOT NULL,
    penalty_dollar REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS penalties (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL,
    amount_dollar  REAL    NOT NULL,
    created_ts_utc TEXT    NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""

def main():
    conn = sqlite3.connect(DB_PATH)
    with conn:
        conn.executescript(SCHEMA)
    conn.close()
    print(f"DB initialized at {DB_PATH}")

if __name__ == "__main__":
    main()
