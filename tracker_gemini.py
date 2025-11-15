import os
import time
import json
import io
from datetime import datetime, UTC
import random

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
USER_NAME = "johnny"      # change as you like
GROUP_NAME = "hackathon"  # change as you like

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
    ts = datetime.now(UTC).isoformat()

    try:
        resp = requests.post(
            f"{API_BASE}/events",
            json={
                "user": USER_NAME,
                "group": GROUP_NAME,
                "label": int(label),
                # backend ignores extra fields, but timestamp is nice to have
                "timestamp": ts,
            },
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
