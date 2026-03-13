# CLAUDE.md

This file provides context for AI assistants working on this repository.

## Project Overview

**DSCOVR:EPIC Image Viewer** -- a Raspberry Pi application that fetches real-time Earth photographs from NASA's Deep Space Climate Observatory (DSCOVR) satellite via the EPIC (Earth Polychromatic Imaging Camera) API and displays them on a small round display.

**Target hardware:** Raspberry Pi Zero W with a 2.1" Hyperpixel Round Touch display (480x480 pixels) from Pimoroni.

**Behavior:** The app polls the NASA EPIC API every 120 minutes for new "Blue Marble" images, downloads and crops them to fit the circular display, then rotates through them every 20 seconds with optional blending transitions.

## Repository Structure

```
.
├── epic.py              # Main application (single-file Python app)
├── requirements.txt     # Python dependencies (pygame, requests)
├── start-epic.sh        # Startup script (sets brightness, launches app)
├── brightness.sh        # Shell script to control Hyperpixel display brightness via GPIO PWM
├── epic.desktop         # Desktop entry for autostart on boot
├── loading.jpg          # Loading splash image shown at startup
├── format_source.cmd    # Windows batch script to run isort + black
├── pyproject.toml       # Black formatter configuration
├── .isort.cfg           # isort import sorting configuration
├── .gitignore           # Ignores venv/, *.jpg (downloaded images), __pycache__/, .idea/
└── readme.md            # User-facing project documentation
```

There is only one source file: `epic.py`. This is intentionally a simple, single-file project.

## Tech Stack

- **Language:** Python 3 (target version: 3.9+)
- **Dependencies:** `pygame==2.1.2` (display/graphics), `requests==2.28.2` (HTTP API calls)
- **Standard library:** `datetime`, `time`, `io`, `json`, `os`, `urllib.request`
- **Hardware:** WiringPi GPIO (via `gpio` shell command in `brightness.sh`)
- **External API:** NASA EPIC API at `https://epic.gsfc.nasa.gov/api/natural`

## Code Architecture (epic.py)

The application is structured as top-level configuration followed by functions and a main loop:

1. **Lines 1-33 -- Initialization:** Imports, pygame setup, display configuration (480x480 fullscreen), loading image display
2. **Lines 17-21 -- Settings block:** `check_delay` (120 min), `rotate_delay` (20 sec), `enable_blending` (True/False), `blending_duration` (5 sec)
3. **`get_epic_images_json()`** -- Calls NASA EPIC API, returns JSON array of image metadata
4. **`create_image_urls(photos)`** -- Constructs full archive URLs from API metadata date strings
5. **`save_photos(imageurls)`** -- Downloads images, crops center 830px square, scales to 480x480, saves as `0.jpg`, `1.jpg`, etc.
6. **`blend_between_photos(old_image, new_image, target_duration)`** -- Smooth fade transition using incremental alpha (0-255) over the target duration
7. **`rotate_photos(num_photos, rotate_delay, blend_enabled, blend_time)`** -- Cycles through saved images with optional blending, handles quit events
8. **Lines 131-173 -- Main loop:** Infinite loop that checks for new images on schedule, downloads when new data is detected, and continuously rotates through saved images

**State tracking:** The main loop uses `last_data` vs `newest_data` (date strings from API) to detect new image sets. Images are cached to disk as numbered `.jpg` files.

## Code Style and Formatting

- **Formatter:** Black with 120-character line length (`pyproject.toml`)
- **Import sorting:** isort with 120-character line length, multi-line mode 3 (vertical hanging indent), trailing commas (`.isort.cfg`)
- **Target Python version:** 3.9 (`pyproject.toml`)
- **String quotes:** Single quotes preferred (Black `skip-string-normalization = true`)

### Running formatters

On Windows, use the provided batch script:
```
format_source.cmd
```

On Linux/macOS, run manually:
```bash
isort . --sp=.isort.cfg
black . --config=pyproject.toml
```

Both tools require a virtual environment with `isort` and `black` installed.

## Build and Run

There is no build step. The application runs directly:

```bash
python3 -u epic.py
```

Or via the startup script (designed for the Pi):
```bash
./start-epic.sh
```

Install dependencies:
```bash
pip3 install -r requirements.txt
```

## Key Conventions

- **Single-file architecture:** All application logic lives in `epic.py`. Keep it that way unless there's a strong reason to split.
- **Settings at the top:** User-configurable values (`check_delay`, `rotate_delay`, `enable_blending`, `blending_duration`) are defined as module-level variables near the top of `epic.py`.
- **Images saved to disk:** Downloaded images are saved as numbered `.jpg` files in the project root (gitignored) rather than held in memory, since this runs on a memory-constrained Pi Zero.
- **Functional decomposition:** Each major operation (API call, URL construction, image download/processing, blending, rotation) has its own function.
- **Minimal dependencies:** Only two third-party packages. Avoid adding dependencies unless necessary.
- **No error handling framework:** The code relies on exceptions propagating naturally. Keep error handling minimal and pragmatic.
- **`loading.jpg` is checked in:** This is the splash image shown while the app first fetches data. Other `.jpg` files are gitignored (they are downloaded satellite images).

## Deployment

The app is deployed to `~pi/code/epic/` on the Raspberry Pi. The `epic.desktop` file is copied to `~/.config/autostart/` for automatic startup on boot. The `start-epic.sh` script sets display brightness to 50% and launches the app with stdout redirected to `/dev/null`.

## Common Tasks

| Task | Command |
|------|---------|
| Run the app | `python3 -u epic.py` |
| Install dependencies | `pip3 install -r requirements.txt` |
| Format code (Linux) | `isort . --sp=.isort.cfg && black . --config=pyproject.toml` |
| Format code (Windows) | `format_source.cmd` |
| Set display brightness | `bash brightness.sh <0-100>` |

## GitHub Issues

Issues labelled `agent` are handled autonomously by the dev loop.

Check open issues:
```bash
TOKEN=$(python3 -c "import json; print(json.load(open('/home/node/.openclaw/workspace/.secrets/github.json'))['GH_TOKEN'])")
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/ikuznetsoff/RaspberryPi-epic/issues?state=open&labels=agent&per_page=20"
```

Create issue:
```bash
TOKEN=$(python3 -c "import json; print(json.load(open('/home/node/.openclaw/workspace/.secrets/github.json'))['GH_TOKEN'])")
curl -s -X POST -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/ikuznetsoff/RaspberryPi-epic/issues \
  -d '{"title":"<title>","body":"<description>","labels":["agent"]}'
```

Close issue:
```bash
TOKEN=$(python3 -c "import json; print(json.load(open('/home/node/.openclaw/workspace/.secrets/github.json'))['GH_TOKEN'])")
curl -s -X PATCH -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/ikuznetsoff/RaspberryPi-epic/issues/<N> \
  -d '{"state":"closed"}'
```

## Autonomous Dev Loop

When triggered by the dev loop cron, Claude Code should:

1. Check open issues labelled `agent` (see above)
2. If no issues → exit silently (NO_REPLY)
3. Pick the highest priority issue and post a plan to Telegram BEFORE starting work:
```bash
BOT=$(python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print(d['channels']['telegram']['botToken'])")
curl -s -X POST "https://api.telegram.org/bot$BOT/sendMessage" \
  -d "chat_id=-1003870229466&message_thread_id=7" \
  --data-urlencode "text=🍓 RaspberryPi-epic loop started

📋 Taking into work:
• #N — title

🔧 Starting..."
```
4. Implement the fix in `epic.py` (single-file project — keep it that way)
5. Format code: `isort . --sp=.isort.cfg && black . --config=pyproject.toml`
6. Run a quick sanity check (imports, syntax): `python3 -c "import ast; ast.parse(open('epic.py').read()); print('OK')"`
7. Commit and push:
```bash
git config user.email "kuznetsoff@gmail.com"
git config user.name "Ivan Kuznetsov"
git add -A
git commit -m "fix: <description> — closes #N"
git push origin main
```
8. Close the issue (see above)
9. Post result to Telegram:
```bash
BOT=$(python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print(d['channels']['telegram']['botToken'])")
curl -s -X POST "https://api.telegram.org/bot$BOT/sendMessage" \
  -d "chat_id=-1003870229466&message_thread_id=7" \
  --data-urlencode "text=🍓 RaspberryPi-epic — done

✅ Closed #N — title
📦 Commit: <hash>"
```

### Rules
- Single issue per loop run
- Keep single-file architecture (epic.py only)
- No new dependencies unless explicitly required by issue
- Post to Telegram topic 7 (🗂 Проекты), not a dedicated channel
