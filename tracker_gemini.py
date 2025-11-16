import os
import time
import json
import io
import random
import getpass
from datetime import datetime, timezone

import requests
import mss
from PIL import Image
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # vision-capable model

# sample every N seconds; we randomize inside the loop too
INTERVAL_SECONDS = random.randint(1, 5) * 20

LOG_FILE = "labels.jsonl"

API_BASE = "http://127.0.0.1:8000"
USER_NAME = getpass.getuser()      # change as you like
GROUP_NAME = "hackathon"           # change as you like

# email: can be set via env or prompted once
USER_EMAIL = os.getenv("USER_EMAIL")  # optional; if None we'll prompt when backend requires it

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. Put it in a .env file or env var."
    )

# Create a single Gemini client (Developer API)
client = genai.Client(api_key=GEMINI_API_KEY)


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
    """

    prompt = (
        "You are classifying a computer screenshot based on how productive the user is.\n\n"
        "You should return a float digit between 0 and 5 (inclusive), where:\n"
        "- 0 = Very Productive (e.g. coding, writing, work documents)\n"
        "- 5 - Very Unproductive (e.g. gaming, gambling sites, wasting time)\n\n"
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
        try:
            label = float(ch)
            return label
        except ValueError:
            continue

    print(f"[WARN] Could not parse label from response text: {text!r}")
    return None


# =========================
# Logging to backend (Supabase via FastAPI)
# =========================

def maybe_prompt_for_email(detail_msg: str | None) -> None:
    """
    If backend says email is required and we don't have one yet, prompt user once.
    """
    global USER_EMAIL

    if USER_EMAIL:
        return

    if detail_msg not in ("Email required for new users", "Email required for this user"):
        return

    while not USER_EMAIL:
        entered = input(
            "\n[SETUP] Backend requires an email for punishment emails.\n"
            "Enter your email (or leave blank to cancel): "
        ).strip()
        if not entered:
            print("[INFO] No email provided; this event will be skipped.")
            return
        USER_EMAIL = entered
        print(f"[INFO] Using email: {USER_EMAIL}")


def log_label(label: int):
    global USER_EMAIL

    # we don't really need this timestamp on backend now, but keep it for logs
    ts = datetime.now(timezone.utc)
    ts_str = ts.isoformat()

    payload = {
        "user": USER_NAME,
        "group": GROUP_NAME,
        "label": int(label),
        "timestamp": ts_str,
        "email": USER_EMAIL,
    }

    url = f"{API_BASE}/events"

    # First attempt
    try:
        resp = requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[ERROR] Failed to send event to backend: {e}")
        return

    # If backend demands email, prompt once and retry
    if resp.status_code == 400:
        try:
            data = resp.json()
            detail = data.get("detail")
        except Exception:
            detail = None

        maybe_prompt_for_email(detail)

        if USER_EMAIL and detail in ("Email required for new users", "Email required for this user"):
            payload["email"] = USER_EMAIL
            try:
                resp = requests.post(url, json=payload, timeout=5)
            except Exception as e:
                print(f"[ERROR] Failed to resend event after email input: {e}")
                return
        else:
            # either user refused email or this is some other 400
            print(f"[ERROR] Backend 400: {resp.text}")
            return

    try:
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Backend error: {e} — body: {resp.text}")
        return

    try:
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] Backend OK but JSON parse failed: {e}")
        return

    # old SQLite backend used "points_for_this_event"
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
        f"[LOG] {ts} -> {USER_NAME}@{GROUP_NAME} label {label} ({data.get('label_name')}) | "
        f"event_points={event_points:.2f}, pool={pool_points:.2f}"
    )


# =========================
# Main loop
# =========================

def main():
    print("Starting productivity tracker (Gemini)…")
    print(f"Model: {GEMINI_MODEL}")
    print(f"Sampling every {INTERVAL_SECONDS} seconds (randomized after each loop).")
    print(f"Logging to {LOG_FILE}")
    print("Press Ctrl+C to stop.\n")

    interval_seconds = INTERVAL_SECONDS

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
