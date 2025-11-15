import sqlite3
from pathlib import Path

DB_PATH = Path("big_brother.db")

INTERVAL_SECONDS = 20  # must match tracker

PENALTY_PER_MINUTE = {
    0: 0.00,  # productive
    1: 0.00,  # other
    2: 0.10,  # shopping
    3: 0.10,  # gaming
    4: 0.20,  # doomscrolling
    5: 0.50,  # jacking off
}

LABEL_NAMES = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "jacking off",
}

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT label, COUNT(*) FROM samples GROUP BY label")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No samples in DB yet.")
        return

    total_minutes = 0.0
    total_penalty = 0.0

    print(f"Summary (assuming {INTERVAL_SECONDS}s per sample):\n")

    for label, count in sorted(rows, key=lambda x: x[0]):
        minutes = count * INTERVAL_SECONDS / 60.0
        rate = PENALTY_PER_MINUTE.get(label, 0.0)
        penalty = minutes * rate

        total_minutes += minutes
        total_penalty += penalty

        print(f"{label} - {LABEL_NAMES[label]:12s}: "
              f"{minutes:6.1f} min,  penalty = ${penalty:5.2f}")

    print()
    print(f"TOTAL time:   {total_minutes:.1f} min")
    print(f"TOTAL penalty: ${total_penalty:.2f}")

if __name__ == "__main__":
    main()
