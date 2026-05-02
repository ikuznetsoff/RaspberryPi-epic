# Interactive Weather Overlay — Design

**Date:** 2026-05-02
**Project:** RaspberryPi-epic
**Status:** Approved (pending spec review)

## Summary

Extend the EPIC photo viewer with a tap-toggled, full-screen, dimmed information overlay
that displays a live clock and current/forecast weather for a configured city. Single-file
architecture preserved. No new third-party dependencies (uses existing `requests`).

## Goals

- Tap the round 480x480 touch display to reveal weather + clock.
- Tap again to dismiss; auto-dismiss after 60 s as a safety net so the picture frame
  cannot stay frozen on the overlay if forgotten.
- Refresh weather in the background; never block rendering on network I/O.
- Survive transient network failures gracefully; never show silently-stale data.

## Non-Goals

- No multi-page overlay (no swiping between cards).
- No editable settings UI on-device — config is in source constants at top of `epic.py`.
- No historical weather, alerts, or radar imagery.
- No new font shipped — use Pi OS default `dejavusans`.

## User-Visible Behavior

### Interaction model

- Default state: `MODE_PHOTO` — photos rotate as today.
- `MOUSEBUTTONDOWN` event toggles between `MODE_PHOTO` and `MODE_OVERLAY`.
- In `MODE_OVERLAY`:
  - Photo rotation is paused.
  - Overlay stays until next tap **or** until 60 s elapse since overlay opened.
  - Clock updates live (re-rendered every frame at 30 FPS).
  - If cached weather is older than 10 min, a one-shot background refresh is kicked off
    on tap; the current cache renders immediately and is replaced when the fetch lands.

### Layout (round-display safe)

```
        ┌─────────────────────┐
        │       12:34         │  clock,        72pt, y≈100
        │                     │
        │       -8°C          │  temperature,  96pt, y≈215
        │   Partly Cloudy      │  condition,    28pt, y≈295
        │                     │
        │  ↑ 06:42  ↓ 19:08   │  sun,          24pt, y≈345
        │  Today  60% · 2mm    │  rain today,   24pt, y≈385
        │  Tomorrow 80% · 5mm  │  rain tmrw,    24pt, y≈420
        └─────────────────────┘
```

Stale-cache indicator (when shown) is rendered as small 16pt muted-yellow text
**directly under the clock** at y≈140 — placed top-center where it remains inside
the safe inner-circle region rather than at the visually clipped bottom edge.

- Background: full-screen black surface at alpha 180 over current photo.
- All text horizontally centered. Vertical positions chosen so all content sits inside
  the visible inner circle (~440 px diameter) of the round bezel. Bottom row at y≈420
  sits ~10 px inside that boundary.
- Font: `pygame.font.SysFont('dejavusans', size)`.
- Color: white text on dimmed Earth. Stale indicator drawn in muted yellow.

### Formatting

- Clock: 24-hour, `HH:MM` (no seconds — reduces visual noise on a still-life device).
- Temperature: Celsius, integer rounded.
- Wind: not shown (intentionally dropped; rain prioritized instead).
- Rain: `precipitation_probability_max` (%) and `precipitation_sum` (mm) for today
  and tomorrow on separate lines. Missing values fall back to `—`.
- Sunrise / sunset: 24-hour `HH:MM`, derived from API timezone-aware fields.

## Architecture

### Single-file structure

`epic.py` remains the only source file. No package split. Tests stay in `test_epic.py`.

### State machine

```
                 MOUSEBUTTONDOWN
            ┌──────────────────────────┐
            │                          ▼
       ┌─────────┐                 ┌──────────────┐
       │  PHOTO  │                 │   OVERLAY    │
       └─────────┘                 └──────────────┘
            ▲                          │
            │                          │
            └──────────────────────────┘
              MOUSEBUTTONDOWN
                  OR
              now >= overlay_dismiss_at  (60 s safety)
```

A separate transient `BLENDING` sub-state of `PHOTO` handles cross-fades during photo
rotation; computed per-frame from elapsed time, no internal sleep loop. Transitions:

- `PHOTO → BLENDING`: when `now >= next_photo_swap_at` **and** `enable_blending` is
  True **and** the next index differs from the current. Sets `blend_started_at = now`.
- `BLENDING → PHOTO`: when `now - blend_started_at >= blending_duration`. Advances
  `current_idx` to the new photo and schedules the next swap.
- `BLENDING → OVERLAY`: a tap during a blend completes the swap immediately (no
  half-faded image left behind), then enters overlay.

### Event-loop refactor

The current `rotate_photos` and `blend_between_photos` block on `time.sleep`. Replace
with a frame-tick model:

- Single `while running` loop in `main()`, paced by `pygame.time.Clock` at 30 FPS.
- Schedules are stored as `datetime` checkpoints:
  - `next_image_api_check_at` — driven by `check_delay`
  - `next_photo_swap_at` — driven by `rotate_delay`
  - `next_weather_refresh_at` — driven by `WEATHER_REFRESH_MIN`
  - `overlay_dismiss_at` — set when entering overlay mode
  - `blend_started_at` / `blend_duration` — used while blending
- Each tick:
  1. Drain `pygame.event.get()` (QUIT, MOUSEBUTTONDOWN).
  2. Evaluate timers; advance state if due.
  3. Render current frame (photo, blend, or photo+overlay).
  4. `clock.tick(30)`.

Rationale:
- Touch latency drops to one frame (~33 ms) instead of up to 20 s.
- Schedules don't drift across long sleeps.
- Clock display updates smoothly while overlay is open.

### Threading model

- Main thread owns pygame, rendering, and all state mutation.
- One **daemon background thread** runs a forever-loop: sleep `WEATHER_REFRESH_MIN * 60`,
  call `fetch_weather`, write to a shared cache dict.
- One **one-shot thread** is started by the main thread on tap when `cache.fetched_at`
  is older than `WEATHER_TAP_REFRESH_MIN`. It calls `fetch_weather` and writes the
  cache, then exits.
- Cache writes are atomic (single-key dict assignment with the GIL). A `threading.Lock`
  guards write-then-read sequences if more than one writer can fire concurrently
  (background + tap-driven); the lock window is a single dict assignment.

## Configuration (top of `epic.py`)

```python
# Existing
check_delay = 120                # minutes — image API poll
rotate_delay = 20                # seconds — photo rotation
enable_blending = True
blending_duration = 5            # seconds

# New
CITY_NAME = 'Warsaw'
WEATHER_REFRESH_MIN = 30         # background refresh cadence
WEATHER_TAP_REFRESH_MIN = 10     # tap kicks one-shot fetch if cache older than this
HTTP_TIMEOUT = 10                # seconds — applied to all outbound HTTP
OVERLAY_AUTO_DISMISS_SEC = 60    # safety net
```

## External APIs

### Geocoding (called once at startup)

`GET https://geocoding-api.open-meteo.com/v1/search?name={CITY_NAME}&count=1`

Response: `{ "results": [{ "latitude": ..., "longitude": ..., "name": ..., "timezone": ... }] }`.

If `results` is empty or the call fails, log a clear error and exit non-zero. A wrong
location is worse than failing loudly.

**Startup order:** geocoding runs **before** `init_display()` so a config error fails
fast without flashing the display, and lat/lon are already known by the time the main
loop starts the background weather thread.

### Weather (called periodically)

`GET https://api.open-meteo.com/v1/forecast` with params:

- `latitude={lat}`
- `longitude={lon}`
- `current=temperature_2m,weather_code`
- `daily=sunrise,sunset,precipitation_probability_max,precipitation_sum`
- `forecast_days=2`
- `timezone=auto`
- `temperature_unit=celsius`

Normalized internal cache shape:

```python
{
  "temp_c": int,                # rounded
  "weather_code": int,          # WMO code
  "condition": str,             # mapped via WMO_CODES
  "sunrise": str,               # "HH:MM" today
  "sunset": str,                # "HH:MM" today
  "rain_today": (prob_pct|None, mm|None),
  "rain_tomorrow": (prob_pct|None, mm|None),
  "fetched_at": datetime,
}
```

### WMO weather code mapping

Inline dict in `epic.py`:

```python
WMO_CODES = {
    0:  'Clear',
    1:  'Mostly Clear',
    2:  'Partly Cloudy',
    3:  'Overcast',
    45: 'Fog',
    48: 'Rime Fog',
    51: 'Light Drizzle',
    53: 'Drizzle',
    55: 'Heavy Drizzle',
    61: 'Light Rain',
    63: 'Rain',
    65: 'Heavy Rain',
    71: 'Light Snow',
    73: 'Snow',
    75: 'Heavy Snow',
    80: 'Showers',
    81: 'Heavy Showers',
    82: 'Violent Showers',
    95: 'Thunderstorm',
    96: 'Thunder w/ Hail',
    99: 'Thunder w/ Heavy Hail',
}
```

Unknown codes render as the integer string.

## Failure Handling

| Scenario                                  | Behavior                                                    |
|-------------------------------------------|-------------------------------------------------------------|
| Geocoding fails at startup                | Log error, exit non-zero. App will not launch.              |
| Weather fetch fails, cache exists         | Show cache, render `⚠ stale HH:MM` indicator.               |
| Weather fetch fails, no cache yet         | Overlay shows clock + `—` placeholders, no stale indicator. |
| HTTP timeout (10 s)                       | Same as fetch fails.                                        |
| Network slow on tap-driven refresh        | Render cached value immediately, swap silently when ready.  |
| EPIC image fetch fails                    | Existing behavior unchanged (wraps later — see Out of Scope). |

"Stale" threshold = `WEATHER_REFRESH_MIN` minutes. If `fetched_at` is older than that,
indicator shows.

## New Functions in `epic.py`

| Function                              | Purpose                                                           |
|---------------------------------------|-------------------------------------------------------------------|
| `geocode_city(name) -> (lat, lon, display_name)` | Startup-only Open-Meteo geocode.                       |
| `fetch_weather(lat, lon) -> dict`     | One HTTP call, returns normalized cache dict.                     |
| `weather_refresh_loop(lat, lon, cache, lock)` | Daemon thread body: sleep + fetch forever.                |
| `kick_tap_refresh(lat, lon, cache, lock)` | Spawns one-shot fetch thread, no-op if recent.                |
| `render_overlay(screen, cache, now)`  | Pure renderer: dim layer + text stack.                            |
| `tick_state(state, events, now)`      | Compute next state from events + timers (testable in isolation).  |

`tick_state` contract: takes a state dataclass/dict (`mode`, `current_idx`, `num_photos`,
all `next_*_at` checkpoints, `overlay_dismiss_at`, `blend_started_at`), a list of pygame
events, and `now`. Returns a new state. Pure function — no I/O, no pygame calls — so it
is the unit-test seam for state-machine logic.

Existing `rotate_photos` and `blend_between_photos` collapse into render helpers
without internal loops:

| Existing function                                      | Replacement                                            |
|--------------------------------------------------------|--------------------------------------------------------|
| `rotate_photos(num_photos, rotate_delay, ...)` (loops) | `render_photo(screen, idx)` (one frame)                |
| `blend_between_photos(old, new, duration, screen)`     | `render_blend(screen, old, new, alpha)` (one frame)    |

## Testing

Existing suite: 99% coverage, 45 tests, single file `test_epic.py`. Maintain ≥95%.

### Strategy

- Keep `test_epic.py` single file.
- Mock `requests.get` for EPIC API, geocoding API, and weather API endpoints.
- Mock `pygame` surface ops as existing tests already do.
- Use `freezegun` **only if** manual `datetime` patching becomes unwieldy — do not add
  the dep speculatively.

### New test groups

1. `geocode_city` — happy, city-not-found, network failure.
2. `fetch_weather` — happy path, missing `precipitation_probability_max`, timeout, normalization.
3. Cache staleness logic — fresh, stale-but-shown, no-cache-yet.
4. `render_overlay` — smoke tests with full data, partial data, no cache.
5. `tick_state` — `PHOTO ↔ OVERLAY` toggle on `MOUSEBUTTONDOWN`, 60 s auto-dismiss.
6. WMO code → text mapping, parametrized including unknown code fallback.
7. Schedule-checkpoint advancement — photo rotation timer, weather refresh timer.

Threaded code is tested by directly invoking the worker function; tests do **not**
spin background threads.

## Out of Scope (deliberately deferred)

- Existing bugs flagged in the code review — `pygame.quit()` in event loops, no HTTP
  timeouts on EPIC fetches, mixed `urllib`/`requests` usage. Some will be touched
  incidentally by the event-loop refactor; broader cleanup is a separate change.
- Multi-language support for condition strings.
- Multiple location support / location switching.
- Touch gesture vocabulary beyond a single tap.

## Open Risks

- **Touch driver**: Hyperpixel Round on Pi OS exposes touch as standard X mouse input,
  which pygame surfaces as `MOUSEBUTTONDOWN`. If the driver instead emits `FINGERDOWN`,
  the event handler must accept both. Verify on-device during implementation.
- **Round-bezel clipping**: y-coordinates above are estimates. Final values to be
  tuned on the physical display; spec accepts ±15 px adjustment without re-approval.
- **Background thread + GIL**: cache writes are simple dict assignments; the lock is
  a guard against the rare case where main thread reads a half-written dict. If
  Python's atomicity guarantees prove insufficient (they should not), upgrade to a
  `queue.Queue` handoff.
