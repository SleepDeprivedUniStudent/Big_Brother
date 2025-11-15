import os
import time
import json
import io
from datetime import datetime
import sqlite3
from pathlib import Path
from datetime import datetime, UTC

#FastAPI
import requests
from datetime import datetime, UTC

import mss
from PIL import Image
from dotenv import load_dotenv

from google import genai
from google.genai import types

import random

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # vision-capable model
INTERVAL_SECONDS =  random.randint(1, 5) * 20  # how often to sample
LOG_FILE = "labels.jsonl"

API_BASE = "http://127.0.0.1:8000"
USER_NAME = "johnny"      # change as you like
GROUP_NAME = "hackathon"  # change as you like

DB_PATH = Path("big_brother.db")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. Put it in a .env file or env var."
    )

# Create a single Gemini client (Developer API)
client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# Screenshot capture (no disk)
# =========================

def capture_screenshot_png_bytes():
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

    # Build image part
    image_part = types.Part.from_bytes(
        data=png_bytes,
        mime_type="image/png",
    )

    try:
        # multimodal generateContent call
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

    # response.text should be the model's text output
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        print("[WARN] Empty response from Gemini")
        return None

    # Extract the first digit 0–5
    for ch in text:
        if ch.isdigit():
            label = int(ch)
            if 0 <= label <= 5:
                return label

    print(f"[WARN] Could not parse label from response text: {text!r}")
    return None


# =========================
# Logging
# =========================

API_BASE = "http://127.0.0.1:8000"

def log_label(label: int):
    ts = datetime.now(UTC).isoformat()

    try:
        resp = requests.post(
            f"{API_BASE}/events",
            json={
                "user": USER_NAME,
                "group": GROUP_NAME,
                "label": int(label),
                "timestamp": ts,
            },
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Failed to send event to backend: {e}")
        return

    data = resp.json()
    print(
        f"[LOG] {ts} -> {USER_NAME}@{GROUP_NAME} label {label} ({data.get('label_name')}) | "
        f"event_points={data.get('points_for_this_event'):.2f}, "
        f"pool={data.get('pool_points'):.2f}"
    )



def init_db():
    if not DB_PATH.exists():
        raise RuntimeError(f"Database {DB_PATH} not found. Run db_init.py first.")

# =========================
# Main loop
# =========================

def main():
    init_db()
    print("Starting productivity tracker (Gemini)…")
    print(f"Model: {GEMINI_MODEL}")
    print(f"Sampling every {INTERVAL_SECONDS} seconds.")
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
