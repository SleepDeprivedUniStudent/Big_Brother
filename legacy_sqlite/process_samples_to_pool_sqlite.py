import sqlite3
from pathlib import Path
from datetime import datetime, UTC, timedelta, date

DB_PATH = Path("big_brother.db")

INTERVAL_SECONDS = 20  # must match tracker_gemini.py

# Points per MINUTE for each label
POINTS_PER_MINUTE = {
    0: 0.0,   # productive
    1: 0.0,   # other
    2: 10.0,  # shopping
    3: 10.0,  # gaming
    4: 20.0,  # doomscrolling
    5: 50.0,  # jacking off
}

def get_week_start(d: date) -> date:
    # Monday as the first day of the week
    return d - timedelta(days=d.weekday())

def get_last_processed_sample_id(conn) -> int | None:
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'last_processed_sample_id'")
    row = cur.fetchone()
    if row:
        try:
            return int(row[0])
        except ValueError:
            return None
    return None

def set_last_processed_sample_id(conn, sample_id: int) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('last_processed_sample_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(sample_id),),
    )

def get_or_create_pool(conn, week_start_str: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pools WHERE week_start_utc_date = ?",
        (week_start_str,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO pools (week_start_utc_date, pool_points) VALUES (?, 0.0)",
        (week_start_str,),
    )
    return cur.lastrowid

def get_or_create_weekly_stats(conn, week_start_str: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM weekly_stats WHERE week_start_utc_date = ?",
        (week_start_str,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "INSERT INTO weekly_stats (week_start_utc_date) VALUES (?)",
        (week_start_str,),
    )
    return cur.lastrowid

def process():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    with conn:
        last_id = get_last_processed_sample_id(conn)

        if last_id is None:
            cur = conn.execute(
                "SELECT id, ts_utc, label FROM samples ORDER BY id ASC"
            )
        else:
            cur = conn.execute(
                "SELECT id, ts_utc, label FROM samples WHERE id > ? ORDER BY id ASC",
                (last_id,),
            )

        rows = cur.fetchall()
        if not rows:
            print("No new samples to process.")
            return

        print(f"Processing {len(rows)} new samples...")
        last_seen_id = last_id

        for row in rows:
            sample_id = row["id"]
            ts_str = row["ts_utc"]
            label = row["label"]
            last_seen_id = sample_id

            # Parse timestamp, compute week
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                # assume stored as UTC naive
                ts = ts.replace(tzinfo=UTC)
            week_start = get_week_start(ts.date())
            week_start_str = week_start.isoformat()

            # Ensure pool + weekly stats row exist
            pool_id = get_or_create_pool(conn, week_start_str)
            stats_id = get_or_create_weekly_stats(conn, week_start_str)

            # Update weekly stats seconds
            duration_sec = float(INTERVAL_SECONDS)

            col_map = {
                0: "productive_seconds",
                1: "other_seconds",
                2: "shopping_seconds",
                3: "gaming_seconds",
                4: "doomscroll_seconds",
                5: "jackingoff_seconds",
            }
            col = col_map.get(label)
            if col is not None:
                conn.execute(
                    f"UPDATE weekly_stats SET {col} = {col} + ? WHERE id = ?",
                    (duration_sec, stats_id),
                )

            # Compute points and update pool + stats only for bad labels
            rate = POINTS_PER_MINUTE.get(label, 0.0)
            if rate <= 0.0:
                continue

            points = rate * (duration_sec / 60.0)

            # Add to pool
            conn.execute(
                "UPDATE pools SET pool_points = pool_points + ? WHERE id = ?",
                (points, pool_id),
            )

            # Add to weekly_stats pool_contrib_points
            conn.execute(
                "UPDATE weekly_stats SET pool_contrib_points = pool_contrib_points + ? WHERE id = ?",
                (points, stats_id),
            )

            # Log contribution event
            conn.execute(
                "INSERT INTO contribution_events (sample_id, ts_utc, label, duration_sec, points) "
                "VALUES (?, ?, ?, ?, ?)",
                (sample_id, ts_str, label, duration_sec, points),
            )

        if last_seen_id is not None:
            set_last_processed_sample_id(conn, last_seen_id)

    conn.close()
    print("Done.")

if __name__ == "__main__":
    process()
