import os
import time
import io
import random
from datetime import datetime, timezone
from pathlib import Path
import getpass

import requests
import mss
from PIL import Image
from dotenv import load_dotenv

from google import genai
from google.genai import types

# =========================
# Env & basic config
# =========================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # override in .env if needed

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. Put it in a .env file or env var."
    )

API_BASE = "http://127.0.0.1:8000"

USER_NAME = getpass.getuser()

# Sample every N seconds; we randomize inside the loop as well
INITIAL_INTERVAL_SECONDS = random.randint(1, 5) * 20

LOG_FILE = "labels.jsonl"  # still here for backwards compat, not strictly required

# Where we remember the join code locally for this machine/user
JOIN_CODE_FILE = Path.home() / ".big_brother_join_code"

# Create a single Gemini client (Developer API)
client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# Join code handling
# =========================

def load_join_code() -> str:
    """
    Join code resolution order:
      1) JOIN_CODE from environment (if set)
      2) value from ~/.big_brother_join_code
      3) prompt the user once and save it to ~/.big_brother_join_code

    Returns "" (empty string) if user does not enter anything.
    """
    # 1) env var
    env_code = os.getenv("JOIN_CODE")
    if env_code:
        return env_code.strip()

    # 2) stored file
    if JOIN_CODE_FILE.exists():
        return JOIN_CODE_FILE.read_text(encoding="utf-8").strip()

    # 3) ask user
    try:
        code = input("Enter group join code (e.g. HACK123), or leave blank for solo mode: ").strip()
    except EOFError:
        code = ""

    if not code:
        print("No join code entered. Running in solo mode (no shared pool / leaderboard).")
        return ""

    # remember for next runs
    JOIN_CODE_FILE.write_text(code, encoding="utf-8")
    print(f"Join code saved to {JOIN_CODE_FILE}")
    return code


JOIN_CODE = load_join_code()


# =========================
# Screenshot capture (no disk)
# =========================

def capture_screenshot_png_bytes() -> bytes:
    """
    Capture the full main display as PNG bytes (in memory only).
    """
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # main display
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()  # PNG bytes


# =========================
# Gemini classification
# =========================

def classify_image_label(png_bytes: bytes) -> int | None:
    """
    Send screenshot to Gemini multimodal model.
    Returns an int label 0–5 or None on error.

    Labels:
      0 = PRODUCTIVE
      1 = OTHER
      2 = SHOPPING
      3 = GAMING
      4 = DOOMSCROLLING
      5 = JACKING OFF
    """

    prompt = (
        "You are classifying a computer screenshot into EXACTLY one category.\n\n"
        "Use the following label definitions:\n\n"
        "0 = PRODUCTIVE — work, study, tools, coding, IDEs, terminals, emails, docs,\n"
        "    spreadsheets, academic videos, note-taking, productivity apps.\n\n"
        "1 = OTHER — desktop, lock screen, wallpaper, file explorer, system settings,\n"
        "    anything not clearly in another category.\n\n"
        "2 = SHOPPING — browsing products, online stores, carts, checkout pages, Amazon,\n"
        "    eBay, AliExpress, Shein, storefronts, product grids, product pages.\n\n"
        "3 = GAMING — video games, game clients, full-screen games, Steam/Epic launchers,\n"
        "    game lobbies, character screens, gameplay, Twitch streaming dashboards.\n\n"
        "4 = DOOMSCROLLING — social media feeds such as TikTok, Instagram feed, X/Twitter\n"
        "    timelines, Reddit scrolling, YouTube Shorts feed, vertical content feeds.\n\n"
        "5 = JACKING OFF — porn websites, explicit sexual content, nude bodies, sexual\n"
        "    thumbnails, explicit video frames, explicit chats, NSFW material.\n\n"
        "Return ONLY a single digit: 0, 1, 2, 3, 4, or 5. No explanation, no spaces."
    )

    image_part = types.Part.from_bytes(
        data=png_bytes,
        mime_type="image/png",
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                image_part,
                prompt,
            ],
        )
    except Exception as e:
        print(f"[ERROR] Gemini request failed: {e}")
        return None

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        print("[WARN] Empty response from Gemini")
        return None

    for ch in text:
        if ch.isdigit():
            label = int(ch)
            if 0 <= label <= 5:
                return label

    print(f"[WARN] Could not parse label from response text: {text!r}")
    return None


# =========================
# Logging to backend (Supabase via FastAPI)
# =========================

def log_label(label: int):
    # Use full ISO timestamp (UTC); backend currently ignores it, but it's useful to log
    ts = datetime.now(timezone.utc).isoformat()

    payload = {
        "user": USER_NAME,
        "label": int(label),
        "timestamp": ts,
    }

    if JOIN_CODE:
        payload["join_code"] = JOIN_CODE
    # else: could fall back to legacy "group" field if you want a local-only group

    try:
        resp = requests.post(
            f"{API_BASE}/events",
            json=payload,
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Failed to send event to backend: {e}")
        return

    try:
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] Backend OK but JSON parse failed: {e}")
        return

    # SQLite backend used "points_for_this_event"
    # Supabase backend uses "points_added"
    raw_points = data.get("points_for_this_event")
    if raw_points is None:
        raw_points = data.get("points_added")

    raw_pool = data.get("pool_points")

    try:
        event_points = float(raw_points) if raw_points is not None else 0.0
    except (TypeError, ValueError):
        event_points = 0.0

    try:
        pool_points = float(raw_pool) if raw_pool is not None else 0.0
    except (TypeError, ValueError):
        pool_points = 0.0

    print(
        f"[LOG] {ts} -> {USER_NAME}@{JOIN_CODE or 'solo'} "
        f"label {label} ({data.get('label_name')}) | "
        f"event_points={event_points:.2f}, pool={pool_points:.2f}"
    )


# =========================
# Main loop
# =========================

def main():
    print("Starting productivity tracker (Gemini)…")
    print(f"Model: {GEMINI_MODEL}")
    if JOIN_CODE:
        print(f"Join code: {JOIN_CODE}")
    else:
        print("No join code (solo mode – no shared leaderboard).")
    print(f"Sampling every {INITIAL_INTERVAL_SECONDS} seconds (randomized after each loop).")
    print(f"Logging to {LOG_FILE}")
    print("Press Ctrl+C to stop.\n")

    interval_seconds = INITIAL_INTERVAL_SECONDS

    while True:
        try:
            print("[INFO] Capturing screenshot…")
            png_bytes = capture_screenshot_png_bytes()

            print("[INFO] Classifying with Gemini…")
            label = classify_image_label(png_bytes)

            if label is None:
                print("[WARN] Classification failed or returned None, skipping log.")
            else:
                log_label(label)

        except KeyboardInterrupt:
            print("\n[INFO] Stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] Unexpected error in loop: {e}")

        time.sleep(interval_seconds)
        interval_seconds = random.randint(1, 5) * 20


if __name__ == "__main__":
    main()
