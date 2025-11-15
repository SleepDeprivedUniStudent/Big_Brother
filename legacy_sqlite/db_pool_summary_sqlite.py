import sqlite3
from pathlib import Path
from datetime import datetime, UTC, timedelta, date

DB_PATH = Path("big_brother.db")

INTERVAL_SECONDS = 20  # just to print time in minutes more nicely

LABEL_NAMES = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "jacking off",
}

def get_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    now = datetime.now(UTC)
    week_start = get_week_start(now.date())
    week_start_str = week_start.isoformat()

    # Pool
    cur = conn.execute(
        "SELECT pool_points FROM pools WHERE week_start_utc_date = ?",
        (week_start_str,),
    )
    pool_row = cur.fetchone()
    pool_points = pool_row["pool_points"] if pool_row else 0.0

    # Weekly stats
    cur = conn.execute(
        "SELECT * FROM weekly_stats WHERE week_start_utc_date = ?",
        (week_start_str,),
    )
    stats = cur.fetchone()
    conn.close()

    print(f"Week starting {week_start_str}")
    print(f"Current pool: {pool_points:.2f} points\n")

    if not stats:
        print("No weekly stats yet.")
        return

    def sec_to_min(sec: float) -> float:
        return sec / 60.0

    print("Your time this week:")
    print(f"- productive   : {sec_to_min(stats['productive_seconds']):6.1f} min")
    print(f"- other        : {sec_to_min(stats['other_seconds']):6.1f} min")
    print(f"- shopping     : {sec_to_min(stats['shopping_seconds']):6.1f} min")
    print(f"- gaming       : {sec_to_min(stats['gaming_seconds']):6.1f} min")
    print(f"- doomscrolling: {sec_to_min(stats['doomscroll_seconds']):6.1f} min")
    print(f"- jacking off  : {sec_to_min(stats['jackingoff_seconds']):6.1f} min")
    print()
    print(f"Your total contribution to pool: {stats['pool_contrib_points']:.2f} points")

if __name__ == "__main__":
    main()
