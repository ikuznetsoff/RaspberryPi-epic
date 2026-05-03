# CLAUDE.md

This file provides context for AI assistants working on this repository.

## Project Overview

**DSCOVR:EPIC Image Viewer** — a Raspberry Pi application that fetches real-time
Earth photographs from NASA's DSCOVR satellite via the EPIC (Earth Polychromatic
Imaging Camera) API and displays them on a 480x480 round display, with a
tap-toggled weather + 24h-forecast overlay.

**Target hardware:** Raspberry Pi Zero W with a 2.1" Hyperpixel Round Touch
display (480x480 pixels) from Pimoroni. Touch is currently unreliable on this
specific HAT/kernel combo (see "Pi runtime gotchas" below) — the app is built
to gracefully run with touch disabled and accept SIGUSR1 to toggle the overlay
remotely instead.

**Behavior:**
- Polls the NASA EPIC API every `check_delay` minutes (120) for new "Blue Marble"
  image sets. Downloads, crops the centre 830 px square, scales to 480x480, and
  saves them as `0.jpg`, `1.jpg`, … in the working directory.
- Rotates through saved photos every `rotate_delay` seconds (20), with optional
  `enable_blending` cross-fade lasting `blending_duration` seconds (5).
- A single tap (or SIGUSR1) opens a dimmed weather overlay: 24h temperature
  curve + rain probability bars/line at top, current temp with a small
  ↑max/↓min stack, condition string, wind, sunrise/sunset, and today/tomorrow
  rain. The overlay auto-dismisses after 60 s if the second tap is missed.
- Weather data: Open-Meteo, no API key. Geocoded once at startup from
  `CITY_NAME` (default `'Warsaw'`).

## Repository Structure

```
.
├── epic.py                              # Main application (single-file)
├── test_epic.py                         # 121 pytest tests, ≥96% coverage
├── requirements.txt                     # pygame, requests
├── start-epic.sh                        # Startup script (sets brightness, launches)
├── brightness.sh                        # WiringPi-based PWM control (broken on Trixie)
├── epic.desktop                         # Desktop autostart entry
├── loading.jpg                          # Splash image shown at startup
├── format_source.cmd                    # Windows: isort + black
├── pyproject.toml                       # Black config, target py3.9
├── .isort.cfg                           # isort config (line length 120)
├── .gitignore
├── readme.md
└── docs/
    ├── CODE_ANALYSIS.md
    └── superpowers/
        ├── specs/2026-05-02-interactive-weather-overlay-design.md
        └── plans/2026-05-02-interactive-weather-overlay-plan.md
```

Single source file: `epic.py`. Single test file: `test_epic.py`. Keep it that
way unless there's a strong reason to split.

## Tech Stack

- **Language:** Python 3.9+ (development happens on 3.12; Pi runs 3.13 on
  Trixie). Tests assume `pygame.font` is available.
- **Third-party deps (PyPI):** `pygame` (or `pygame-ce` — interchangeable),
  `requests`. Optional: `numpy` for 16bpp framebuffer mode (not needed on the
  current Pi setup, which is 32bpp).
- **Standard library:** `datetime`, `time`, `io`, `json`, `os`, `sys`,
  `struct`, `threading`, `dataclasses`, `urllib.request`, `signal`, `fcntl`,
  `mmap` (the last two only used inside `_open_fb`, lazily imported so tests
  can run on Windows).
- **External APIs:**
  - NASA EPIC: `https://epic.gsfc.nasa.gov/api/natural` + archive at
    `https://epic.gsfc.nasa.gov/archive/natural/...`
  - Open-Meteo geocoding: `https://geocoding-api.open-meteo.com/v1/search`
  - Open-Meteo forecast: `https://api.open-meteo.com/v1/forecast` with
    `hourly=temperature_2m,precipitation_probability` and daily fields.

## Code Architecture (epic.py)

The file is single-file but logically structured in this top-to-bottom order:

1. **Imports + module constants.** All settings at the top: `check_delay`,
   `rotate_delay`, `enable_blending`, `blending_duration`, `CITY_NAME`,
   `WEATHER_REFRESH_MIN`, `WEATHER_TAP_REFRESH_MIN`, `HTTP_TIMEOUT`,
   `OVERLAY_AUTO_DISMISS_SEC`, `DISPLAY_SIZE`, `CROP_SIZE`, `CROP_OFFSET`,
   `WMO_CODES` mapping, fb ioctl numbers, `_FB` global.
2. **Helpers:** `weather_code_to_text`, `geocode_city`, `_safe_index`,
   `_parse_hhmm`, `fetch_weather`, `is_weather_stale`.
3. **State machine:** `MODE_PHOTO`/`MODE_BLENDING`/`MODE_OVERLAY` constants,
   `AppState` dataclass, `_advance_photo`, `tick_state` (pure function — the
   primary unit-test seam).
4. **Render helpers:** `render_photo`, `render_blend`, `compute_blend_alpha`,
   `_format_temp`, `_format_rain`, `_format_sun`, `_format_wind`,
   `render_forecast_chart`, `render_overlay`, `_get_temp_range`,
   `_select_next_24h`.
5. **EPIC + image fetching:** `get_epic_images_json`, `create_image_urls`,
   `save_photos`.
6. **Display + framebuffer:** `_is_windowed_dev_mode`, `_open_fb`,
   `_push_to_fb`, `_present`, `_install_sigusr1_tap`,
   `_start_evdev_touch_reader`, `init_display`.
7. **Background work:** `_maybe_kick_tap_refresh`, `_weather_refresh_loop`,
   `_maybe_check_for_new_images`.
8. **`main()`** — geocode, init display, kick refresh thread, then the
   30 FPS frame-tick event loop.

### Frame-tick event loop

The original implementation used `time.sleep`-driven inner loops in
`rotate_photos` / `blend_between_photos`. That was replaced with a single
30 FPS loop driven by `pygame.time.Clock`. Schedules are tracked as
`datetime` checkpoints inside `AppState`:

- `next_image_api_check_at` — when to poll the EPIC API
- `next_photo_swap_at` — when to advance to the next photo (or start a blend)
- `overlay_dismiss_at` — when the 60 s safety auto-dismiss fires
- `blend_started_at` — start time of the current cross-fade

`tick_state(state, events, now, …)` is **pure**: it takes state + events +
clock + config and returns new state. No I/O, no pygame. All branching is in
this function (mode transitions, blend completion, overlay dismiss). Tests
exercise it directly.

### Weather threading

A daemon thread (`_weather_refresh_loop`) runs forever, sleeping
`WEATHER_REFRESH_MIN * 60` seconds between fetches. A second one-shot thread
spawns from `_maybe_kick_tap_refresh` when a tap occurs and the cache is older
than `WEATHER_TAP_REFRESH_MIN`. A `threading.Lock` guards cache writes; the
main thread reads under the same lock when rendering the overlay.

If a fetch fails, the previous cache stays in place and `is_weather_stale`
drives a small `⚠ stale HH:MM` indicator in the overlay.

### Display modes (init_display)

`init_display()` picks one of three paths:

1. **`EPIC_FBDEV=/dev/fbN` (production / Pi Zero on Trixie):**
   `SDL_VIDEODRIVER=dummy`, pygame draws into an in-memory surface, and
   `_present(screen)` mmaps the framebuffer and pushes pixels directly. No X
   server, no DRM, no SDL backend needed. Required because Trixie's SDL2
   ships without `x11` / `kmsdrm` / `fbcon` backends. Auto-detects 16bpp
   (RGB565, requires numpy) vs 32bpp (BGRA) via `FBIOGET_VSCREENINFO` ioctl.
2. **`EPIC_WINDOWED=1` (or running on win32/darwin):** windowed pygame in
   480x480. ESC quits. Used for dev preview.
3. **Otherwise:** SDL fullscreen. Path that worked on older Pi OS versions
   with X.

### Input

In `EPIC_FBDEV` mode, the SDL `dummy` driver does not poll input devices, so
two extra mechanisms inject `MOUSEBUTTONDOWN` events into pygame's queue:

- **Touch via evdev:** `_start_evdev_touch_reader` reads
  `/dev/input/event0` (override with `EPIC_TOUCH_DEV`), filters for
  `BTN_TOUCH` press events, applies a quiet-period filter
  (`EPIC_TOUCH_QUIET_MS`, default 800 ms — must have at least this much
  silence before accepting a press) and a debounce (`EPIC_TOUCH_DEBOUNCE_MS`,
  default 350 ms). Set `EPIC_NO_TOUCH=1` to skip the reader entirely.
- **SIGUSR1:** `_install_sigusr1_tap` posts a synthetic `MOUSEBUTTONDOWN`
  whenever the process receives SIGUSR1. Toggle the overlay over SSH with
  `pkill -USR1 -f epic.py`.

## Code Style and Formatting

- **Formatter:** Black, line length 120, target Python 3.9, `skip-string-
  normalization = true` (single-quote preferred).
- **Imports:** isort, multi-line mode 3, line length 120, trailing commas.
- Run formatters before committing.

### Running formatters

Windows: `format_source.cmd`.

Linux/macOS:
```bash
isort . --sp=.isort.cfg
black . --config=pyproject.toml
```

## Build and Run

No build step. Three ways to run depending on environment:

### Local dev (Windows/macOS):
```bash
pip install -r requirements.txt
python -u epic.py    # auto-detects platform → windowed, 480x480
```

### Pi Zero W on Trixie (production):
```bash
cd ~/pi/code/epic
source venv/bin/activate
sudo systemctl stop getty@tty1.service           # avoid console fighting fb
EPIC_FBDEV=/dev/fb0 sudo -E env "PATH=$PATH" python -u epic.py
```
- `sudo -E env "PATH=$PATH"` keeps the venv python on PATH while running as
  root (needed to write `/dev/fb0` and read `/dev/input/event*`).
- Touch is currently unreliable on this hardware; add `EPIC_NO_TOUCH=1` and
  use `pkill -USR1 -f epic.py` from another SSH session to toggle the overlay.

### Tests:
```bash
pytest -q                                # 121 tests, ~1s on a laptop
pytest --cov=epic --cov-report=term      # coverage (target ≥95%)
```

## Pi runtime gotchas (Trixie + Hyperpixel 2.1 Round Touch)

These took a long debugging session to nail down. If you're working on
Pi-side issues, check these first.

### `/boot/firmware/config.txt`

Working configuration (KMS off, legacy fbdev path, Pimoroni overlay):
```
# Hyperpixel 2.1 Round panel
#dtoverlay=vc4-kms-v3d                          # disabled — claims pins/fb wrong
dtoverlay=hyperpixel2r                          # Pimoroni legacy DPI overlay
enable_dpi_lcd=1
dpi_group=2
dpi_mode=87
dpi_output_format=0x7f216
dpi_timings=480 0 10 16 55 480 0 15 60 15 0 0 0 60 0 19200000 6
disable_fw_kms_setup=1
#dtoverlay=vc4-kms-dpi-hyperpixel2r             # disabled — colors mapped wrong
dtparam=i2c_arm=on
framebuffer_width=480
framebuffer_height=480
```

Without `framebuffer_width/height=480`, the firmware creates a 720x480 fb
and feeds it to the DPI peripheral — output is garbled because the panel
expects 480x480.

### Panel SPI init

The panel needs a one-time SPI init sequence at boot to set RGB ordering and
wake the controller. Otherwise output shows diagonal stripes and color
artifacts. Pimoroni's `hyperpixel2r-init.service` does this.

The init service uses the original `RPi.GPIO` (which talks to `/dev/mem`
directly). On Bookworm/Trixie, the apt-shipped `python3-rpi-lgpio` shim
fails with `'GPIO not allocated'` because the DPI overlay holds the pins
exclusively via lgpio's chipgpio_claim model. **Fix:**
```bash
sudo apt remove -y python3-rpi-lgpio
sudo pip3 install --break-system-packages --force-reinstall RPi.GPIO
sudo systemctl restart hyperpixel2r-init.service
```

### Framebuffer device

After the above config, two fb devices appear:
- `/dev/fb0` — simplefb (firmware boot logo) — **also where the Pimoroni
  overlay routes pixels in this configuration**, despite the name. **Use
  this one** with `EPIC_FBDEV=/dev/fb0`.
- `/dev/fb1` — secondary, do not use.

Confirm with `fbset -fb /dev/fbN` (look for `geometry 480 480 …`) and a
quick `sudo head -c $((480*480*4)) /dev/urandom > /dev/fbN` — whichever
shows visible noise on the panel is the right one.

### SDL2 has no display backends

Trixie ships SDL2 without `x11`, `wayland`, `kmsdrm`, or `fbcon` — only
`dummy` and `offscreen` work. This is why `EPIC_FBDEV` mode exists.
`pygame-ce` and `pygame` from PyPI both link against the system SDL2 on
armv6l, so neither helps. Don't waste time installing X.

### Touch is noisy

The `ft5x06` kernel driver registers the touch chip at I²C bus 11 address
0x15 — but emits constant phantom press-release pairs at ~12 ms intervals
even with no finger on the screen. `EPIC_TOUCH_QUIET_MS=800` suppresses
flashing, at the cost of real taps almost never landing because there's no
quiet window. **Operational stance: run with `EPIC_NO_TOUCH=1` and use
SIGUSR1.** A future fix would be a USB push button on a GPIO pin.

### Backlight

`brightness.sh` calls deprecated WiringPi `gpio` command — broken on
Trixie. The HAT's backlight is on GPIO 19 PWM. The init service drives it
to full automatically; it does not currently support runtime brightness
control on this OS. Fixing `brightness.sh` to use `pinctrl` is a deferred
task.

### `start-epic.sh` and autostart

The shipped `start-epic.sh` redirects stdout to `/dev/null` and depends on
the desktop autostart firing — neither works on Pi OS Lite Trixie. For
production, prefer a systemd service (see `docs/superpowers/...` plan) or
launch manually over SSH after stopping `getty@tty1.service`.

## Key Conventions

- **Single-file architecture:** All app logic in `epic.py`. All tests in
  `test_epic.py`. Keep it that way unless there's a strong reason.
- **Settings at the top:** All knobs are module-level constants near the top
  of `epic.py`. Don't bury config in nested code.
- **TDD:** Tests cover everything except the daemon thread loop and a few
  branches inside `main()`. Maintain ≥95% coverage. New behavior gets a test
  first.
- **Pure functions for state:** `tick_state`, `compute_blend_alpha`,
  `is_weather_stale`, `_select_next_24h`, `_get_temp_range` are pure — easy
  to unit-test, easy to reason about. Keep new state-machine logic pure.
- **Images saved to disk:** Numbered `.jpg` files in cwd. Pi Zero is
  memory-constrained.
- **Minimal dependencies:** Only pygame + requests. Anything else needs
  justification.
- **Threads only for blocking I/O:** Weather fetches in a daemon thread,
  evdev touch reader in another. Render + state stay on the main thread.
- **Single quotes for strings.** Black is configured to leave them alone.

## Deployment

Production deployment lives at `/home/ivank/pi/code/epic/` on the Pi (the
Pi user is `ivank`, not the Raspberry-default `pi`). The recommended runtime
is a systemd service running as root with `EPIC_FBDEV=/dev/fb0` and
`EPIC_NO_TOUCH=1` set in the unit's `Environment=` block, after
`hyperpixel2r-init.service` has finished. The service file lives outside
this repo (per-Pi system config).

## Common Tasks

| Task | Command |
|------|---------|
| Run app (Pi production) | `EPIC_FBDEV=/dev/fb0 EPIC_NO_TOUCH=1 sudo -E env "PATH=$PATH" python -u epic.py` |
| Run app (dev, any OS) | `python -u epic.py` (windowed on win32/darwin, fullscreen on Linux desktop) |
| Toggle overlay over SSH | `pkill -USR1 -f epic.py` |
| Run tests | `pytest -q` |
| Run tests with coverage | `pytest --cov=epic --cov-report=term-missing` |
| Format (Linux) | `isort . --sp=.isort.cfg && black . --config=pyproject.toml` |
| Format (Windows) | `format_source.cmd` |
| Sanity-parse epic.py | `python -c "import ast; ast.parse(open('epic.py').read()); print('OK')"` |
| Confirm fb / touch on Pi | `fbset -fb /dev/fb0 ; sudo i2cdetect -y 11 ; cat /proc/bus/input/devices` |
| Re-run panel SPI init | `sudo /usr/bin/hyperpixel2r-init` |

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
5. Add tests in `test_epic.py` matching the change. Maintain ≥95% coverage.
6. Format code: `isort . --sp=.isort.cfg && black . --config=pyproject.toml`
7. Run tests: `pytest -q`
8. Sanity check: `python3 -c "import ast; ast.parse(open('epic.py').read()); print('OK')"`
9. Commit and push:
```bash
git config user.email "kuznetsoff@gmail.com"
git config user.name "Ivan Kuznetsov"
git add -A
git commit -m "fix: <description> — closes #N"
git push origin main
```
10. Close the issue (see above)
11. Post result to Telegram:
```bash
BOT=$(python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print(d['channels']['telegram']['botToken'])")
curl -s -X POST "https://api.telegram.org/bot$BOT/sendMessage" \
  -d "chat_id=-1003870229466&message_thread_id=7" \
  --data-urlencode "text=🍓 RaspberryPi-epic — done

✅ Closed #N — title
📦 Commit: <hash>"
```

### Rules
- Single issue per loop run.
- Keep single-file architecture (epic.py only). Tests in test_epic.py.
- No new dependencies unless explicitly required by the issue.
- Post to Telegram topic 7 (🗂 Проекты), not a dedicated channel.
- Don't add backwards-compat shims for removed code.
- Never write multi-line/multi-paragraph comments. One-line max, only when
  the WHY is non-obvious.
