from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3
from pathlib import Path
from datetime import datetime, UTC, timedelta, date
from fastapi import Query

DB_PATH = Path("big_brother.db")

INTERVAL_SECONDS = 20  # must match tracker sampling interval

# Points per MINUTE for each label
POINTS_PER_MINUTE = {
    0: 0.0,   # productive
    1: 0.0,   # other
    2: 10.0,  # shopping
    3: 10.0,  # gaming
    4: 20.0,  # doomscrolling
    5: 50.0,  # jacking off
}

LABEL_NAMES = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "jacking off",
}


# ========= DB SETUP =========

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT    NOT NULL,
    label        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pools (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start_utc_date  TEXT    NOT NULL,
    pool_points          REAL    NOT NULL DEFAULT 0.0,
    UNIQUE (week_start_utc_date)
);

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

CREATE TABLE IF NOT EXISTS contribution_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id       INTEGER NOT NULL,
    ts_utc          TEXT    NOT NULL,
    label           INTEGER NOT NULL,
    duration_sec    REAL    NOT NULL,
    points          REAL    NOT NULL,
    FOREIGN KEY (sample_id) REFERENCES samples(id)
);
"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    with conn:
        conn.executescript(SCHEMA)
    conn.close()


def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_week_start(d: date) -> date:
    # Monday-based week
    return d - timedelta(days=d.weekday())




def get_or_create_pool(conn, group_name: str, week_start_str: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pools WHERE group_name = ? AND week_start_utc_date = ?",
        (group_name, week_start_str),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO pools (group_name, week_start_utc_date, pool_points) VALUES (?, ?, 0.0)",
        (group_name, week_start_str),
    )
    return cur.lastrowid


def get_or_create_weekly_stats(conn, user_name: str, group_name: str, week_start_str: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM weekly_stats WHERE user_name = ? AND group_name = ? AND week_start_utc_date = ?",
        (user_name, group_name, week_start_str),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO weekly_stats (user_name, group_name, week_start_utc_date) VALUES (?, ?, ?)",
        (user_name, group_name, week_start_str),
    )
    return cur.lastrowid



# ========= FASTAPI APP =========

app = FastAPI()


class Event(BaseModel):
    user: str              # e.g. "johnny"
    group: str
    label: int                  # 0–5
    timestamp: Optional[str] = None  # ISO string (optional)


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/events")
def post_event(event: Event):
    """
    Called by your tracker whenever it records a label.
    Now supports multiple users & groups by simple string IDs.
    """
    if event.label < 0 or event.label > 5:
        raise HTTPException(status_code=400, detail="label must be between 0 and 5")

    user_name = event.user.strip() or "anonymous"
    group_name = event.group.strip() or "default"

    # Parse timestamp, default to now UTC
    if event.timestamp:
        try:
            ts = datetime.fromisoformat(event.timestamp)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid timestamp format")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    else:
        ts = datetime.now(UTC)

    week_start = get_week_start(ts.date())
    week_start_str = week_start.isoformat()
    ts_str = ts.isoformat()

    conn = get_db_conn()
    try:
        with conn:
            # 1) Insert sample
            cur = conn.execute(
                "INSERT INTO samples (ts_utc, label) VALUES (?, ?)",
                (ts_str, event.label),
            )
            sample_id = cur.lastrowid

            # 2) Ensure pool + weekly stats row exist
            pool_id = get_or_create_pool(conn, group_name, week_start_str)
            stats_id = get_or_create_weekly_stats(conn, user_name, group_name, week_start_str)

            # 3) Update weekly stats seconds
            duration_sec = float(INTERVAL_SECONDS)

            col_map = {
                0: "productive_seconds",
                1: "other_seconds",
                2: "shopping_seconds",
                3: "gaming_seconds",
                4: "doomscroll_seconds",
                5: "jackingoff_seconds",
            }
            col = col_map.get(event.label)
            if col is not None:
                conn.execute(
                    f"UPDATE weekly_stats SET {col} = {col} + ? WHERE id = ?",
                    (duration_sec, stats_id),
                )

            # 4) Compute points (only for bad labels)
            rate = POINTS_PER_MINUTE.get(event.label, 0.0)
            points = 0.0
            if rate > 0.0:
                points = rate * (duration_sec / 60.0)

                # Add to pool
                conn.execute(
                    "UPDATE pools SET pool_points = pool_points + ? WHERE id = ?",
                    (points, pool_id),
                )

                # Add to weekly_stats pool_contrib_points
                conn.execute(
                    "UPDATE weekly_stats "
                    "SET pool_contrib_points = pool_contrib_points + ? "
                    "WHERE id = ?",
                    (points, stats_id),
                )

                # Log contribution event with user & group
                conn.execute(
                    "INSERT INTO contribution_events (sample_id, ts_utc, label, duration_sec, points, user_name, group_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sample_id, ts_str, event.label, duration_sec, points, user_name, group_name),
                )

            # Fetch current pool + this user's stats to return
            pool_row = conn.execute(
                "SELECT pool_points FROM pools WHERE id = ?",
                (pool_id,),
            ).fetchone()

            stats_row = conn.execute(
                "SELECT * FROM weekly_stats WHERE id = ?",
                (stats_id,),
            ).fetchone()

        return {
            "ok": True,
            "user": user_name,
            "group": group_name,
            "label": event.label,
            "label_name": LABEL_NAMES[event.label],
            "timestamp": ts_str,
            "week_start": week_start_str,
            "points_for_this_event": points,
            "pool_points": pool_row["pool_points"] if pool_row else 0.0,
            "weekly_stats": {
                "productive_seconds": stats_row["productive_seconds"],
                "other_seconds": stats_row["other_seconds"],
                "shopping_seconds": stats_row["shopping_seconds"],
                "gaming_seconds": stats_row["gaming_seconds"],
                "doomscroll_seconds": stats_row["doomscroll_seconds"],
                "jackingoff_seconds": stats_row["jackingoff_seconds"],
                "pool_contrib_points": stats_row["pool_contrib_points"],
            },
        }
    finally:
        conn.close()


@app.get("/week-summary")
def get_week_summary(group: str = Query("default")):
    """
    Return current week's pool + per-user weekly stats for a given group.
    """
    group_name = group.strip() or "default"

    conn = get_db_conn()
    now = datetime.now(UTC)
    week_start = get_week_start(now.date())
    week_start_str = week_start.isoformat()

    # Pool for this group
    pool_row = conn.execute(
        "SELECT pool_points FROM pools WHERE group_name = ? AND week_start_utc_date = ?",
        (group_name, week_start_str),
    ).fetchone()

    pool_points = pool_row["pool_points"] if pool_row else 0.0

    # All user stats for this group/week
    rows = conn.execute(
        "SELECT * FROM weekly_stats WHERE group_name = ? AND week_start_utc_date = ?",
        (group_name, week_start_str),
    ).fetchall()

    conn.close()

    users = []
    for r in rows:
        users.append({
            "user_name": r["user_name"],
            "productive_seconds": r["productive_seconds"],
            "other_seconds": r["other_seconds"],
            "shopping_seconds": r["shopping_seconds"],
            "gaming_seconds": r["gaming_seconds"],
            "doomscroll_seconds": r["doomscroll_seconds"],
            "jackingoff_seconds": r["jackingoff_seconds"],
            "pool_contrib_points": r["pool_contrib_points"],
        })

    # Sort leaderboard by least pool_contrib, then most productive
    users_sorted = sorted(
        users,
        key=lambda u: (u["pool_contrib_points"], -u["productive_seconds"])
    )

    return {
        "week_start": week_start_str,
        "group": group_name,
        "pool_points": pool_points,
        "leaderboard": users_sorted,
    }