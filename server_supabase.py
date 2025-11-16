from datetime import datetime, timedelta, timezone
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client

import random
import string

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


class CreateGroupIn(BaseModel):
    user: str          # username of the creator (unused for now except existence)
    group_name: str    # human-readable name


class CreateGroupOut(BaseModel):
    join_code: str
    group_id: str
    group_name: str


# --------------------
# Helpers
# --------------------

def get_week_start(dt: datetime):
    monday = dt - timedelta(days=dt.weekday())
    return monday.date()


def generate_join_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def ensure_user(username: str) -> str:
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data:
        return res.data[0]["id"]
    inserted = supabase.table("users").insert({"username": username}).execute()
    return inserted.data[0]["id"]


def ensure_group_by_name(group_name: str) -> str:
    res = supabase.table("groups").select("*").eq("group_name", group_name).execute()
    if res.data:
        return res.data[0]["id"]
    inserted = supabase.table("groups").insert({"group_name": group_name}).execute()
    return inserted.data[0]["id"]


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


def resolve_group(event: "EventIn") -> tuple[str, str]:
    """
    Returns (group_id, group_display_name).
    Prefers join_code; falls back to group name for backward compatibility.
    Handles both 'group_name' and 'name' columns.
    """
    # Prefer join_code if provided
    if event.join_code:
        resp = (
            supabase.table("groups")
            .select("*")
            .eq("join_code", event.join_code)
            .execute()
        )
        rows = resp.data or []

        # If group already exists for this join_code, use it
        if rows:
            group_row = rows[0]
            group_id = group_row["id"]
            group_name = (
                group_row.get("group_name")
                or group_row.get("name")
                or group_row.get("join_code")
                or event.join_code
            )
            return group_id, group_name

        # Otherwise, AUTO-CREATE a new group for this join_code
        insert_payload = {
            # if your table has "group_name", this will fill it
            # if it only has "name" instead, we can tweak later
            "group_name": f"Group {event.join_code}",
            "join_code": event.join_code,
        }
        inserted = supabase.table("groups").insert(insert_payload).execute()
        group_row = inserted.data[0]
        group_id = group_row["id"]
        group_name = (
            group_row.get("group_name")
            or group_row.get("name")
            or group_row.get("join_code")
            or event.join_code
        )
        return group_id, group_name

    # Legacy: group name instead of join_code
    if event.group:
        resp = (
            supabase.table("groups")
            .select("*")
            .or_("group_name.eq.{g},name.eq.{g}".format(g=event.group))
            .execute()
        )
        rows = resp.data or []
        if rows:
            group_row = rows[0]
            group_id = group_row["id"]
            group_name = (
                group_row.get("group_name")
                or group_row.get("name")
                or event.group
            )
            return group_id, group_name

        # If not found, create a new row with best-effort naming
        insert_payload = {
            "group_name": event.group,
        }
        inserted = supabase.table("groups").insert(insert_payload).execute()
        group_id = inserted.data[0]["id"]
        return group_id, event.group

    # Neither join_code nor group provided
    raise HTTPException(
        status_code=400,
        detail="Must provide join_code or group",
    )


# --------------------
# FastAPI models & app
# --------------------

class EventIn(BaseModel):
    user: str
    label: int
    timestamp: datetime | None = None

    # New fields:
    join_code: str | None = None       # preferred
    group: str | None = None           # legacy fallback


app = FastAPI(title="Big Brother Supabase Backend")


@app.get("/")
def root():
    return {"status": "ok", "backend": "supabase"}


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



@app.post("/events")
def post_event(event: EventIn):
    now = datetime.now(timezone.utc)
    week_start_date = get_week_start(now)
    week_start_str = str(week_start_date)

    # map label
    label_name = LABEL_NAME.get(event.label, "unknown")
    cost = LABEL_COST.get(event.label, 0.0)
    seconds_field = SECONDS_FIELD.get(event.label)

    # 1) ensure user & group (via join_code or group)
    user_id = ensure_user(event.user)
    group_id, group_display_name = resolve_group(event)

    # 2) ensure weekly pool
    pool_row = ensure_weekly_pool(group_id, week_start_str)
    current_pool = float(pool_row.get("pool_points") or 0.0)
    new_pool = current_pool + cost

    supabase.table("weekly_pools").update(
        {"pool_points": new_pool}
    ).eq("id", pool_row["id"]).execute()

    # 3) insert event row
    supabase.table("events").insert(
        {
            "user_id": user_id,
            "group_id": group_id,
            "label": event.label,
            "label_name": label_name,
            "points": cost,
            "timestamp": now.isoformat(),
            "week_start": week_start_str,
        }
    ).execute()

    # 4) ensure weekly stats + update seconds & points
    stats_row = ensure_weekly_stats(user_id, group_id, week_start_str)

    update_payload = {}
    if seconds_field is not None:
        current_seconds = float(stats_row.get(seconds_field) or 0.0)
        update_payload[seconds_field] = current_seconds + INTERVAL_SECONDS

    current_points = float(stats_row.get("pool_contrib_points") or 0.0)
    update_payload["pool_contrib_points"] = current_points + cost

    supabase.table("weekly_stats").update(update_payload).eq("id", stats_row["id"]).execute()

    return {
        "ok": True,
        "user": event.user,
        "group": group_display_name,
        "group_join_code": event.join_code,
        "label": event.label,
        "label_name": label_name,
        "points_added": cost,
        "week_start": week_start_str,
        "pool_points": new_pool,
    }

from typing import Optional

@app.get("/week-summary")
def week_summary(
    join_code: Optional[str] = None,
    group: Optional[str] = None,
):
    now = datetime.now(timezone.utc)
    week_start_date = get_week_start(now)
    week_start_str = str(week_start_date)

    # Resolve group either by join_code or by name
    if join_code:
        resp = (
            supabase.table("groups")
            .select("*")
            .eq("join_code", join_code)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return {
                "week_start": week_start_str,
                "group": join_code,
                "pool_points": 0.0,
                "leaderboard": [],
            }
        group_row = rows[0]
        group_id = group_row["id"]
        group_name = (
            group_row.get("group_name")
            or group_row.get("name")
            or group_row.get("join_code")
            or join_code
        )
    elif group:
        resp = (
            supabase.table("groups")
            .select("*")
            .or_("group_name.eq.{g},name.eq.{g}".format(g=group))
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return {
                "week_start": week_start_str,
                "group": group,
                "pool_points": 0.0,
                "leaderboard": [],
            }
        group_row = rows[0]
        group_id = group_row["id"]
        group_name = (
            group_row.get("group_name")
            or group_row.get("name")
            or group
        )
    else:
        raise HTTPException(status_code=400, detail="Must provide join_code or group")

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
    leaderboard.sort(
        key=lambda row: (
            float(row.get("pool_contrib_points") or 0.0),
            float(row.get("productive_seconds") or 0.0),
        ),
        reverse=True,
    )

    return {
        "week_start": week_start_str,
        "group": group_name,
        "pool_points": pool_points,
        "leaderboard": leaderboard,
    }
