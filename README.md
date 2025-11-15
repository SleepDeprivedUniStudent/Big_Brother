# Big Brother Productivity Tracker

A productivity tracker that captures screenshots periodically, classifies the activity using Google's Gemini multimodal model, logs the results, and maintains weekly stats with points/penalties per activity type.

---

## Features

- Screenshots computer activity at regular intervals
- Classifies screenshots into categories:
  - `0 = Productive`
  - `1 = Other`
  - `2 = Shopping`
  - `3 = Gaming`
  - `4 = Doomscrolling`
  - `5 = Jacking Off`
- Logs events to a local FastAPI backend
- Tracks weekly stats per user and group
- Computes "pool points" and penalties for unproductive activity
- Multi-user and multi-group support

---

## Requirements

To set up and run the tracker, you will need two separate terminals.

### 1. Setup and Installation

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file to store API keys:

```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.0-flash
```

### 3. Running the Application

**Terminal A (Start FastAPI Backend):**

```bash
cd ~/Big_Brother
source .venv/bin/activate
uvicorn server:app --reload
```

**Terminal B (Start Productivity Tracker):**

```bash
cd ~/Big_Brother
source .venv/bin/activate
python tracker_gemini.py
```

---

## Usage

Once both terminals are running, the tracker will automatically begin capturing and classifying screenshots at the configured intervals. Check the FastAPI backend for logged events and weekly statistics.