import sqlite3
from pathlib import Path

DB_PATH = Path("big_brother.db")

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Add group_name to pools if not exists
    cur.execute("PRAGMA table_info(pools)")
    cols = [row[1] for row in cur.fetchall()]
    if "group_name" not in cols:
        print("Adding group_name to pools...")
        cur.execute("ALTER TABLE pools ADD COLUMN group_name TEXT NOT NULL DEFAULT 'default'")

    # Add user_name and group_name to weekly_stats if not exists
    cur.execute("PRAGMA table_info(weekly_stats)")
    cols = [row[1] for row in cur.fetchall()]
    if "user_name" not in cols:
        print("Adding user_name to weekly_stats...")
        cur.execute("ALTER TABLE weekly_stats ADD COLUMN user_name TEXT NOT NULL DEFAULT 'you'")
    if "group_name" not in cols:
        print("Adding group_name to weekly_stats...")
        cur.execute("ALTER TABLE weekly_stats ADD COLUMN group_name TEXT NOT NULL DEFAULT 'default'")

    # Add user_name and group_name to contribution_events if not exists
    cur.execute("PRAGMA table_info(contribution_events)")
    cols = [row[1] for row in cur.fetchall()]
    if "user_name" not in cols:
        print("Adding user_name to contribution_events...")
        cur.execute("ALTER TABLE contribution_events ADD COLUMN user_name TEXT NOT NULL DEFAULT 'you'")
    if "group_name" not in cols:
        print("Adding group_name to contribution_events...")
        cur.execute("ALTER TABLE contribution_events ADD COLUMN group_name TEXT NOT NULL DEFAULT 'default'")

    conn.commit()
    conn.close()
    print("Migration done.")

if __name__ == "__main__":
    main()
