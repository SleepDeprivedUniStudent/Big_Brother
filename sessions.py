import json
from datetime import datetime
from collections import namedtuple

INTERVAL_SECONDS = 20

PENALTY_PER_MINUTE = {
    0: 0.00,
    1: 0.00,
    2: 0.10,
    3: 0.10,
    4: 0.20,
    5: 0.50,
}

LABEL_NAMES = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "jacking off",
}

Session = namedtuple("Session", ["label", "start", "end", "samples"])

def parse_timestamp(ts_str):
    # From ISO string to datetime; your tracker uses naive UTC ISO strings.
    return datetime.fromisoformat(ts_str)

def load_records(path="labels.jsonl"):
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = rec.get("timestamp")
            label = rec.get("label")
            if ts and isinstance(label, int) and 0 <= label <= 5:
                records.append((parse_timestamp(ts), label))
    records.sort(key=lambda x: x[0])
    return records

def build_sessions(records, min_duration_seconds=60):
    if not records:
        return []

    sessions = []
    current_label = records[0][1]
    start_time = records[0][0]
    prev_time = start_time
    sample_count = 1

    for ts, label in records[1:]:
        if label == current_label:
            # same session
            prev_time = ts
            sample_count += 1
        else:
            # close previous session
            end_time = prev_time
            duration = (end_time - start_time).total_seconds()
            if duration >= min_duration_seconds:
                sessions.append(Session(current_label, start_time, end_time, sample_count))
            # start new session
            current_label = label
            start_time = ts
            prev_time = ts
            sample_count = 1

    # close last session
    end_time = prev_time
    duration = (end_time - start_time).total_seconds()
    if duration >= min_duration_seconds:
        sessions.append(Session(current_label, start_time, end_time, sample_count))

    return sessions

def main():
    records = load_records()
    if not records:
        print("No data in labels.jsonl yet.")
        return

    # Minimum session length (e.g. 2 minutes)
    MIN_SESSION_SECONDS = 120

    sessions = build_sessions(records, min_duration_seconds=MIN_SESSION_SECONDS)

    if not sessions:
        print(f"No sessions longer than {MIN_SESSION_SECONDS} seconds.")
        return

    print(f"Sessions longer than {MIN_SESSION_SECONDS} seconds:\n")

    total_penalty = 0.0

    for s in sessions:
        duration_sec = (s.end - s.start).total_seconds()
        duration_min = duration_sec / 60.0
        rate = PENALTY_PER_MINUTE.get(s.label, 0.0)
        penalty = duration_min * rate
        total_penalty += penalty

        print(
            f"{LABEL_NAMES[s.label]:12s}: "
            f"{s.start} -> {s.end} "
            f"({duration_min:5.1f} min, penalty ${penalty:5.2f})"
        )

    print()
    print(f"Total penalty from sessions: ${total_penalty:.2f}")

if __name__ == "__main__":
    main()
