import json
from collections import Counter, defaultdict

# Match INTERVAL_SECONDS from tracker_gemini.py
INTERVAL_SECONDS = 20

# Penalty per MINUTE for each label
PENALTY_PER_MINUTE = {
    0: 0.00,  # productive
    1: 0.01,  # other
    2: 0.10,  # shopping
    3: 0.20,  # gaming
    4: 0.30,  # doomscrolling
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
    counts = Counter()

    with open("labels.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            label = rec.get("label")
            if isinstance(label, int) and 0 <= label <= 5:
                counts[label] += 1

    if not counts:
        print("No data in labels.jsonl yet.")
        return

    print("Summary (assuming {}s per sample):".format(INTERVAL_SECONDS))
    print()

    total_minutes = 0.0
    total_penalty = 0.0
    per_label_minutes = {}
    per_label_penalty = {}

    for label in range(0, 6):
        n = counts.get(label, 0)
        minutes = n * INTERVAL_SECONDS / 60.0
        per_label_minutes[label] = minutes
        penalty_rate = PENALTY_PER_MINUTE.get(label, 0.0)
        penalty = minutes * penalty_rate
        per_label_penalty[label] = penalty

        total_minutes += minutes
        total_penalty += penalty

        print(f"{label} - {LABEL_NAMES[label]:12s}: "
              f"{minutes:6.1f} min,  penalty = ${penalty:5.2f}")

    print()
    print(f"TOTAL time:   {total_minutes:.1f} min")
    print(f"TOTAL penalty: ${total_penalty:.2f}")

if __name__ == "__main__":
    main()
