from datetime import datetime, timedelta, timezone
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from supabase import create_client

# --------------------
# Env & Supabase client
# --------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set correctly in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --------------------
# Config
# --------------------

# label mapping:
# 0 = productive, 1 = other, 2 = shopping, 3 = gaming, 4 = doomscrolling, 5 = jacking off
LABEL_NAME = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "jackingoff",
}

# map label -> money/points per event (you can tweak these)
LABEL_COST = {
    0: 0.0,   # productive
    1: 0.1,   # other
    2: 0.1,   # shopping
    3: 0.3,   # gaming
    4: 0.2,   # doomscrolling
    5: 0.5,   # jacking off
}

# map label -> column name in weekly_stats
SECONDS_FIELD = {
    0: "productive_seconds",
    1: "other_seconds",
    2: "shopping_seconds",
    3: "gaming_seconds",
    4: "doomscroll_seconds",      # NOTE: table column is doomscroll_seconds
    5: "jackingoff_seconds",
}

INTERVAL_SECONDS = 20  # each tracker tick

# --------------------
# Helpers
# --------------------

def get_week_start(dt: datetime):
    monday = dt - timedelta(days=dt.weekday())
    return monday.date()


def ensure_user(username: str) -> str:
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data:
        return res.data[0]["id"]
    inserted = supabase.table("users").insert({"username": username}).execute()
    return inserted.data[0]["id"]


def ensure_group(group_name: str) -> str:
    res = supabase.table("groups").select("*").eq("group_name", group_name).execute()
    if res.data:
        return res.data[0]["id"]
    inserted = supabase.table("groups").insert({"group_name": group_name}).execute()
    return inserted.data[0]["id"]


def ensure_membership(user_id: str, group_id: str):
    res = (
        supabase.table("group_members")
        .select("*")
        .eq("user_id", user_id)
        .eq("group_id", group_id)
        .execute()
    )
    if not res.data:
        supabase.table("group_members").insert(
            {"user_id": user_id, "group_id": group_id}
        ).execute()


def ensure_weekly_pool(group_id: str, week_start: str) -> dict:
    res = (
        supabase.table("weekly_pools")
        .select("*")
        .eq("group_id", group_id)
        .eq("week_start", week_start)
        .execute()
    )
    if res.data:
        return res.data[0]
    inserted = (
        supabase.table("weekly_pools")
        .insert(
            {
                "group_id": group_id,
                "week_start": week_start,
                "pool_points": 0,
            }
        )
        .execute()
    )
    return inserted.data[0]


def ensure_weekly_stats(user_id: str, group_id: str, week_start: str) -> dict:
    res = (
        supabase.table("weekly_stats")
        .select("*")
        .eq("user_id", user_id)
        .eq("group_id", group_id)
        .eq("week_start", week_start)
        .execute()
    )
    if res.data:
        return res.data[0]
    inserted = (
        supabase.table("weekly_stats")
        .insert(
            {
                "user_id": user_id,
                "group_id": group_id,
                "week_start": week_start,
            }
        )
        .execute()
    )
    return inserted.data[0]

def ensure_active_round(group_id: str) -> dict:
    """
    Ensure there is an active game_rounds row for this group.
    If none exists, create a new round with the next round_number.
    """
    now = datetime.now(timezone.utc)

    # Try to find an existing active round
    res = (
        supabase.table("game_rounds")
        .select("*")
        .eq("group_id", group_id)
        .eq("status", "active")
        .order("start_time", desc=False)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if rows:
        return rows[0]

    # No active round found – create a new one with next round_number
    max_round_res = (
        supabase.table("game_rounds")
        .select("round_number")
        .eq("group_id", group_id)
        .order("round_number", desc=True)
        .limit(1)
        .execute()
    )
    max_rows = max_round_res.data or []
    if max_rows:
        next_round_number = (max_rows[0].get("round_number") or 0) + 1
    else:
        next_round_number = 1

    insert_payload = {
        "group_id": group_id,
        "round_number": next_round_number,
        "start_time": now.isoformat(),
        "status": "active",
        "pool": 0,
    }
    inserted = supabase.table("game_rounds").insert(insert_payload).execute()
    return inserted.data[0]


def ensure_round_stats(round_id: str, user_id: str) -> dict:
    """
    Ensure there is a round_stats row for this (round_id, user_id).
    """
    res = (
        supabase.table("round_stats")
        .select("*")
        .eq("round_id", round_id)
        .eq("user_id", user_id)
        .execute()
    )
    rows = res.data or []
    if rows:
        return rows[0]

    inserted = (
        supabase.table("round_stats")
        .insert(
            {
                "round_id": round_id,
                "user_id": user_id,
            }
        )
        .execute()
    )
    return inserted.data[0]

# --------------------
# FastAPI models & app
# --------------------

class EventIn(BaseModel):
    user: str
    group: str
    label: int


app = FastAPI(title="Big Brother Supabase Backend")


@app.get("/")
def root():
    return {"status": "ok", "backend": "supabase"}


<<<<<<< Updated upstream
=======
@app.post("/create-group", response_model=CreateGroupOut)
def create_group(payload: CreateGroupIn):
    username = payload.user.strip()
    group_name = payload.group_name.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not group_name:
        raise HTTPException(status_code=400, detail="Group name is required")

    # 1) Ensure user exists
    user_res = (
        supabase.table("users")
        .select("id")
        .eq("username", username)
        .execute()
    )
    rows = user_res.data or []
    if rows:
        user_id = rows[0]["id"]
    else:
        insert_user = supabase.table("users").insert({"username": username}).execute()
        user_id = insert_user.data[0]["id"]

    # 2) Generate a unique join code
    while True:
        code = generate_join_code(6)
        existing = (
            supabase.table("groups")
            .select("id")
            .eq("join_code", code)
            .execute()
        )
        if not (existing.data or []):
            join_code = code
            break

    # 3) Create the group
    group_insert = supabase.table("groups").insert(
        {
            # support either "group_name" or "name" in your actual schema
            "group_name": group_name,
            "join_code": join_code,
            # "created_by_user_id": user_id,  # only if you have this column
        }
    ).execute()
    group_id = group_insert.data[0]["id"]

    # 4) Optionally add creator as member if group_members table exists
    try:
        supabase.table("group_members").insert(
            {
                "group_id": group_id,
                "user_id": user_id,
                "role": "admin",
            }
        ).execute()
    except Exception:
        # If you don't have group_members yet, silently ignore
        pass

    return CreateGroupOut(
        join_code=join_code,
        group_id=group_id,
        group_name=group_name,
    )

>>>>>>> Stashed changes
@app.post("/events")
def post_event(event: EventIn):
    now = datetime.now(timezone.utc)
    week_start_date = get_week_start(now)
    week_start_str = str(week_start_date)

    # map label -> metadata
    label_name = LABEL_NAME.get(event.label, "unknown")
    cost = LABEL_COST.get(event.label, 0.0)
    seconds_field = SECONDS_FIELD.get(event.label)

    # 1) ensure user & group
    user_id = ensure_user(event.user)
    group_id = ensure_group(event.group)
    ensure_membership(user_id, group_id)

    # 2) ensure active round & per-user round stats
    round_row = ensure_active_round(group_id)
    round_id = round_row["id"]

    # Update this round's total pool
    current_round_pool = float(round_row.get("pool") or 0.0)
    new_round_pool = current_round_pool + cost
    supabase.table("game_rounds").update(
        {"pool": new_round_pool}
    ).eq("id", round_id).execute()

    # Ensure per-user round stats and update their current_score
    round_stats_row = ensure_round_stats(round_id, user_id)
    current_score = float(round_stats_row.get("current_score") or 0.0)
    new_score = current_score + cost
    supabase.table("round_stats").update(
        {"current_score": new_score}
    ).eq("id", round_stats_row["id"]).execute()

    # 3) ensure weekly pool (classic weekly view)
    pool_row = ensure_weekly_pool(group_id, week_start_str)
    current_pool = float(pool_row.get("pool_points") or 0.0)
    new_pool = current_pool + cost

    supabase.table("weekly_pools").update(
        {"pool_points": new_pool}
    ).eq("id", pool_row["id"]).execute()

    # 4) insert event row
    event_ts = event.timestamp or now
    supabase.table("events").insert(
        {
            "user_id": user_id,
            "group_id": group_id,
            "label": event.label,
            "label_name": label_name,
            "points": cost,
            "timestamp": event_ts.isoformat(),
            "week_start": week_start_str,
        }
    ).execute()

    # 5) ensure weekly stats + update seconds & pool_contrib_points
    stats_row = ensure_weekly_stats(user_id, group_id, week_start_str)

    update_payload: dict = {}
    if seconds_field is not None:
        current_seconds = float(stats_row.get(seconds_field) or 0.0)
        update_payload[seconds_field] = current_seconds + INTERVAL_SECONDS

    current_points = float(stats_row.get("pool_contrib_points") or 0.0)
    update_payload["pool_contrib_points"] = current_points + cost

    supabase.table("weekly_stats").update(update_payload).eq(
        "id", stats_row["id"]
    ).execute()

    # Response used by tracker_gemini.py
    return {
        "ok": True,
        "user": event.user,
<<<<<<< Updated upstream
        "group": event.group,
=======
        "user_id": user_id,
        "group": group_display_name,
        "group_join_code": event.join_code,
>>>>>>> Stashed changes
        "label": event.label,
        "label_name": label_name,
        "points_added": cost,
        "week_start": week_start_str,
        "pool_points": new_pool,          # weekly pool (for that group + week)
        # New: round-related info
        "round_id": round_id,
        "round_number": round_row.get("round_number"),
        "round_pool": new_round_pool,     # pool for this active round
        "user_round_score": new_score,    # this user's current_score in this round
    }


@app.get("/week-summary")
def week_summary(group: str):
    now = datetime.now(timezone.utc)
    week_start_date = get_week_start(now)
    week_start_str = str(week_start_date)

    # find group
    group_res = (
        supabase.table("groups")
        .select("*")
        .eq("group_name", group)
        .execute()
    )
    if not group_res.data:
        return {
            "week_start": week_start_str,
            "group": group,
            "pool_points": 0.0,
            "leaderboard": [],
        }

    group_id = group_res.data[0]["id"]

    # pool
    pool_res = (
        supabase.table("weekly_pools")
        .select("*")
        .eq("group_id", group_id)
        .eq("week_start", week_start_str)
        .execute()
    )
    pool_points = 0.0
    if pool_res.data:
        pool_points = float(pool_res.data[0].get("pool_points") or 0.0)

    # weekly stats
    stats_res = (
        supabase.table("weekly_stats")
        .select("*")
        .eq("group_id", group_id)
        .eq("week_start", week_start_str)
        .execute()
    )

    leaderboard = stats_res.data or []
    # sort by pool_contrib_points desc, then productive_seconds desc
    leaderboard.sort(
        key=lambda row: (
            float(row.get("pool_contrib_points") or 0.0),
            float(row.get("productive_seconds") or 0.0),
        ),
        reverse=True,
    )

    return {
        "week_start": week_start_str,
        "group": group,
        "pool_points": pool_points,
        "leaderboard": leaderboard,
    }
