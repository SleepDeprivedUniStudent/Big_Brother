from datetime import datetime, timedelta, timezone
import os
import random
import string
from typing import Optional, List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# ====================
# Env & Supabase client
# ====================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set correctly in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Email provider (Resend)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Big Brother <no-reply@example.com>")


# ====================
# Pydantic models
# ====================

class EventIn(BaseModel):
    user: str
    group: str
    label: int
    timestamp: Optional[str] = None  # currently unused, but accepted
    email: Optional[str] = None      # optional; required by backend logic for new users


class CreateGroupIn(BaseModel):
    user: str          # username of the creator
    group_name: str    # human-readable name
    email: Optional[str] = None      # optional; will be required if user is new


class CreateGroupOut(BaseModel):
    join_code: str
    group_id: str
    group_name: str


# ====================
# Config
# ====================

ROUND_LENGTH = timedelta(minutes=5)
# label mapping:
# 0 = productive, 1 = other, 2 = shopping, 3 = gaming, 4 = doomscrolling, 5 = gambling
LABEL_NAME = {
    0: "productive",
    1: "other",
    2: "shopping",
    3: "gaming",
    4: "doomscrolling",
    5: "gambling",
}

# map label -> money/points per event (you can tweak these)
LABEL_COST = {
    0: 0.0,   # productive
    1: 0.1,   # other
    2: 0.1,   # shopping
    3: 0.3,   # gaming
    4: 0.2,   # doomscrolling
    5: 0.5,   # gambling
}

# map label -> column name in weekly_stats
SECONDS_FIELD = {
    0: "productive_seconds",
    1: "other_seconds",
    2: "shopping_seconds",
    3: "gaming_seconds",
    4: "doomscroll_seconds",      # NOTE: table column is doomscroll_seconds
    5: "gambling_seconds",
}

INTERVAL_SECONDS = 20  # each tracker tick (seconds)


# ====================
# Helpers
# ====================

def get_week_start(dt: datetime):
    monday = dt - timedelta(days=dt.weekday())
    return monday.date()


def generate_join_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def ensure_user(username: str, email: Optional[str] = None, require_email: bool = False) -> str:
    """
    Find or create a user by username.

    Option 3 logic:
      - If user exists:
          - If require_email and neither DB nor argument has email, raise 400.
          - If we get a new email and DB has none, fill it in.
      - If user does not exist:
          - If require_email and email is missing, raise 400.
          - Otherwise create with username and optional email.
    """
    res = (
        supabase.table("users")
        .select("id, email")
        .eq("username", username)
        .execute()
    )
    rows = res.data or []
    if rows:
        user_id = rows[0]["id"]
        existing_email = rows[0].get("email")

        if require_email and not (existing_email or email):
            raise HTTPException(status_code=400, detail="Email required for this user")

        # backfill email if we just learned it
        if email and not existing_email:
            supabase.table("users").update({"email": email}).eq("id", user_id).execute()

        return user_id

    # brand new user
    if require_email and not email:
        raise HTTPException(status_code=400, detail="Email required for new users")

    insert_data = {"username": username}
    if email:
        insert_data["email"] = email

    inserted = supabase.table("users").insert(insert_data).execute()
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

def get_or_cycle_active_round(group_id: str) -> dict:
    """
    This is the main game engine. It finds the active round,
    ends it if its time is up, and creates a new one.
    It always returns the current, valid, active round.
    """
    now = datetime.now(timezone.utc)

    # 1. Try to find an existing active round
    res = (
        supabase.table("game_rounds")
        .select("*")
        .eq("group_id", group_id)
        .eq("status", "active")
        .order("start_time", desc=True)
        .limit(1)
        .execute()
    )
    
    active_round = res.data[0] if res.data else None

    # 2. Check if the found round is expired
    # --- THIS IS THE FIX ---
    if active_round:
        # Parse the end_time string from the DB
        end_time_from_db = datetime.fromisoformat(active_round["end_time"])
        
        # Make the naive datetime "aware" by telling it it's UTC
        aware_end_time = end_time_from_db.replace(tzinfo=timezone.utc)

        if now > aware_end_time:
            # --- TIME IS UP! END THE OLD ROUND ---
            print(f"Ending round {active_round['id']}...")
            
            # Find the winner (lowest score)
            winner_res = (
                supabase.table("round_stats")
                .select("user_id, current_score")
                .eq("round_id", active_round["id"])
                .order("current_score", desc=False) # False = ASC (lowest score wins)
                .limit(1)
                .execute()
            )
            
            winner_id = winner_res.data[0]["user_id"] if winner_res.data else None
            
            # Update the old round
            supabase.table("game_rounds").update(
                {"status": "finished", "winner_id": winner_id}
            ).eq("id", active_round["id"]).execute()
            
            print(f"Winner is {winner_id}")
            
            # Set active_round to None so we create a new one
            active_round = None 

    # 3. If no round exists (or we just ended one), create a new one
    if not active_round:
        print("Creating new round...")
        # Find the next round_number
        max_round_res = (
            supabase.table("game_rounds")
            .select("round_number")
            .eq("group_id", group_id)
            .order("round_number", desc=True)
            .limit(1)
            .execute()
        )
        next_round_number = (max_round_res.data[0]["round_number"] + 1) if max_round_res.data else 1
        
        # Create the new round
        end_time = now + ROUND_LENGTH
        insert_payload = {
            "group_id": group_id,
            "round_number": next_round_number,
            "start_time": now.isoformat(),
            "end_time": end_time.isoformat(),
            "status": "active",
            "pool": 0,
            "winner_id": None
        }
        inserted = supabase.table("game_rounds").insert(insert_payload).execute()
        active_round = inserted.data[0]

    # 4. Return the guaranteed active round
    return active_round


def ensure_round_stats(round_id: str, user_id: str) -> dict: # <-- 1. Add argument
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
                "current_score": 0.0,
            }
        )
        .execute()
    )
    return inserted.data[0]


# ====================
# Email helpers
# ====================

def send_email(to_email: str, subject: str, body: str):
    """
    Send a plain-text email using Resend.
    """
    if not RESEND_API_KEY:
        print("[EMAIL] RESEND_API_KEY not set; skipping email.")
        return

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "text": body,
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            print(f"[EMAIL] Resend error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[EMAIL] Failed to send email: {e}")


def get_round_peers(round_id: str, triggering_user_id: str) -> List[dict]:
    """
    Get all other users in the same round who have a non-null email.
    """
    res = (
        supabase.table("round_stats")
        .select("user_id")
        .eq("round_id", round_id)
        .execute()
    )
    rows = res.data or []
    peer_ids = [r["user_id"] for r in rows if r["user_id"] != triggering_user_id]
    if not peer_ids:
        return []

    users_res = (
        supabase.table("users")
        .select("id, username, email")
        .in_("id", peer_ids)
        .execute()
    )
    users = users_res.data or []
    return [u for u in users if u.get("email")]


def should_notify(label: int) -> bool:
    """
    Only send emails for labels 2–5.
    """
    return label in (2, 3, 4, 5)


def notify_unproductive_round(
    round_id: str,
    triggering_user_id: str,
    label: int,
    label_name: str,
    cost: float,
    group_name: str,
):
    """
    Send extremely informal roast emails to everyone else in the round
    for labels 2–5.
    """
    if not should_notify(label):
        return

    peers = get_round_peers(round_id, triggering_user_id)
    if not peers:
        return

    # Get the username of the person who triggered the event
    trig_res = (
        supabase.table("users")
        .select("username")
        .eq("id", triggering_user_id)
        .limit(1)
        .execute()
    )
    trig_row = (trig_res.data or [None])[0]
    trigger_name = (trig_row or {}).get("username") or "your homie"

    # label-specific roast lines
    ROASTS = {
        "shopping":      "is shopping",
        "gaming":        "is gaming",
        "doomscrolling": "is doomscrolling again",
        "gambling":      "is blowing their savings on sports bets",
    }

    roast_line = ROASTS.get(label_name, f"did some {label_name} nonsense")

    for u in peers:
        to_email = u.get("email")
        peer_name = u.get("username") or "bro"

        subject = f"ayo {peer_name}, your homie slipped up 🤣"

        body = (
            f"yo {peer_name},\n\n"
            f"your homie {trigger_name} {roast_line}\n"
            f"and Big Brother just drained ${cost:.2f} from their wallet.\n\n"
            f"Group: {group_name}\n"
            f"Round ID: {round_id}\n\n"
            f"this is wild. check the leaderboard before they lose more money lmao.\n"
        )

        send_email(to_email, subject, body)


# ====================
# FastAPI app
# ====================

app = FastAPI(title="Big Brother Supabase Backend")


#app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

@app.get("/")
def serve_frontend():
    """Serves the main frontend HTML file."""
    return FileResponse('frontend/index.html')


@app.post("/create-group", response_model=CreateGroupOut)
def create_group(payload: CreateGroupIn):
    username = payload.user.strip()
    group_name = payload.group_name.strip()
    email = (payload.email or "").strip() or None

    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not group_name:
        raise HTTPException(status_code=400, detail="Group name is required")

    # Ensure user exists & enforce email requirement when new
    user_id = ensure_user(username, email, require_email=True)

    # Generate a unique join code
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

    # Create the group
    group_insert = supabase.table("groups").insert(
        {
            "group_name": group_name,
            "join_code": join_code,
        }
    ).execute()
    group_id = group_insert.data[0]["id"]

    # Add creator as admin member
    try:
        supabase.table("group_members").insert(
            {
                "group_id": group_id,
                "user_id": user_id,
                "role": "admin",
            }
        ).execute()
    except Exception:
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

    # map label -> metadata
    label_name = LABEL_NAME.get(event.label, "unknown")
    cost = LABEL_COST.get(event.label, 0.0)
    seconds_field = SECONDS_FIELD.get(event.label)

    # 1) ensure user & email requirement (Option 3)
    email = (event.email or "").strip() or None
    user_id = ensure_user(event.user, email, require_email=False)

    # 2) ensure group + membership
    group_id = ensure_group(event.group)
    ensure_membership(user_id, group_id)

    # 2) ensure active round & per-user round stats
    round_row = get_or_cycle_active_round(group_id)
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

    # 4) ensure weekly pool (classic weekly view)
    pool_row = ensure_weekly_pool(group_id, week_start_str)
    current_pool = float(pool_row.get("pool_points") or 0.0)
    new_pool = current_pool + cost

    supabase.table("weekly_pools").update(
        {"pool_points": new_pool}
    ).eq("id", pool_row["id"]).execute()

    # 5) insert event row
    event_ts = now  # using server time as source of truth
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

    # 6) ensure weekly stats + update seconds & pool_contrib_points
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

    # 7) notify round peers (labels 2–5 only)
    try:
        notify_unproductive_round(
            round_id=round_id,
            triggering_user_id=user_id,
            label=event.label,
            label_name=label_name,
            cost=cost,
            group_name=event.group,
        )
    except Exception as e:
        print(f"[EMAIL] notify_unproductive_round failed: {e}")

    # Response used by tracker_gemini.py
    return {
        "ok": True,
        "user": event.user,
        "user_id": user_id,
        "group": event.group,
        "label": event.label,
        "label_name": label_name,
        "points_added": cost,
        "week_start": week_start_str,
        "pool_points": new_pool,          # weekly pool
        "round_id": round_id,
        "round_number": round_row.get("round_number"),
        "round_pool": new_round_pool,     # pool for this round
        "user_round_score": new_score,    # this user's score in this round
    }

@app.get("/round-summary")
def get_round_summary(group: str):
    """
    Get the live status of the current round, the leaderboard,
    and the winner of the last round.
    """
    now = datetime.now(timezone.utc)

    # 1. Find the group
    group_res = (
        supabase.table("groups")
        .select("id, group_name")
        .eq("group_name", group)
        .limit(1)
        .execute()
    )
    if not group_res.data:
        raise HTTPException(status_code=404, detail="Group not found")
    
    group_id = group_res.data[0]["id"]
    group_name = group_res.data[0]["group_name"]

    # 2. Find the current active round
    active_round_res = (
        supabase.table("game_rounds")
        .select("*")
        .eq("group_id", group_id)
        .eq("status", "active")
        .order("start_time", desc=True)
        .limit(1)
        .execute()
    )
    
    active_round = active_round_res.data[0] if active_round_res.data else None

    # 3. Find the last finished round's winner
    last_winner_name = None
    last_finished_round_res = (
        supabase.table("game_rounds")
        .select("winner_id")
        .eq("group_id", group_id)
        .eq("status", "finished")
        .order("end_time", desc=True) # Get the most recent finished round
        .limit(1)
        .execute()
    )
    
    if last_finished_round_res.data:
        winner_id = last_finished_round_res.data[0].get("winner_id")
        if winner_id:
            winner_res = (
                supabase.table("users")
                .select("username")
                .eq("id", winner_id)
                .limit(1)
                .execute()
            )
            if winner_res.data:
                last_winner_name = winner_res.data[0].get("username")

    # 4. If no active round, return a "waiting" state
    if not active_round:
        return {
            "group_name": group_name,
            "active_round": None,
            "leaderboard": [],
            "last_winner": {"username": last_winner_name} if last_winner_name else None,
            "message": "No active round. Waiting for first event..."
        }

    # 5. Get the live leaderboard for the active round
    # This query joins "round_stats" with "users" to get usernames
    leaderboard_res = (
        supabase.table("round_stats")
        .select("current_score, users(username)") # Magic join!
        .eq("round_id", active_round["id"])
        .order("current_score", desc=False) # False = ASC (lowest score wins)
        .execute()
    )

    # 6. Format the leaderboard data for the frontend
    leaderboard_data = leaderboard_res.data or []
    leaderboard = []
    for row in leaderboard_data:
        # The join returns: {"current_score": 0.5, "users": {"username": "johnny"}}
        username = "Unknown"
        if row.get("users"): # Check if the 'users' object exists
            username = row["users"].get("username", "Unknown")
            
        leaderboard.append({
            "username": username,
            "score": row.get("current_score", 0.0)
        })

    # 7. Assemble and return the final JSON
    return {
        "group_name": group_name,
        "active_round": {
            "round_id": active_round["id"],
            "round_number": active_round.get("round_number"),
            "pool": active_round.get("pool"),
            "start_time": active_round.get("start_time"),
            "end_time": active_round.get("end_time")
        },
        "leaderboard": leaderboard,
        "last_winner": {"username": last_winner_name} if last_winner_name else None
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
