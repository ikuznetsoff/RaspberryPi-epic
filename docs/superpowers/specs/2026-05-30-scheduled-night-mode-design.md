# Scheduled Night Mode — Design

**Date:** 2026-05-30
**Status:** Approved, pending implementation
**Feature:** Time-of-day scheduler that turns the display on during the day and
off at night.

## Problem

The EPIC viewer runs 24/7 on a Pi Zero W. At night the lit panel is a nuisance
(bedroom glow) and the device does pointless work rotating photos nobody sees.
We want the screen automatically off overnight and back on in the morning, on a
fixed daily schedule. Default: **on 08:00–22:00, off 22:00–08:00**.

## Constraints

- Single-file app (`epic.py`), single test file (`test_epic.py`). Keep it that
  way.
- No new third-party dependencies. `subprocess` (stdlib) is allowed.
- Backlight PWM control via `brightness.sh` (WiringPi) is **broken on Trixie**.
  Runtime backlight control is unproven on this Hyperpixel 2.1 Round + DPI
  overlay combo, so it must be best-effort with a guaranteed software fallback.
- Maintain ≥95% test coverage. New behaviour gets a test first (TDD).
- Keep state logic in pure functions, matching the existing `tick_state` /
  `is_weather_stale` convention.

## Decisions

1. **Off mechanism = black frame + best-effort backlight off.** Push an
   all-black frame (guaranteed, pure software) *and* try `pinctrl set <gpio> op
   dl` to cut the GPIO19 backlight for true darkness. If `pinctrl` is missing or
   fails, the black frame still applies — the screen is dark content on a faint
   backlight rather than nothing.
2. **Night ignores input.** While off, taps and SIGUSR1 do nothing. The screen
   only wakes when the schedule says so. Simplest, most predictable.
3. **Night logic lives outside `tick_state`.** It is orthogonal to photo
   rotation / blending / overlay. A pure predicate `is_screen_on(now, on, off)`
   plus a thin edge-triggered branch in the main loop. `tick_state` stays
   unaware of night mode.

## Configuration

New module-level constants near the top of `epic.py`, each with an env override
so a per-Pi schedule needs no code edit:

| Constant | Default | Env override | Meaning |
|---|---|---|---|
| `SCREEN_ON` | `'08:00'` | `EPIC_SCREEN_ON` | Daily wake time (HH:MM, 24h) |
| `SCREEN_OFF` | `'22:00'` | `EPIC_SCREEN_OFF` | Daily sleep time (HH:MM, 24h) |
| `NIGHT_MODE` | `True` | `EPIC_NIGHT_DISABLE=1` | Set to disable scheduling (always on) |
| `BACKLIGHT_GPIO` | `19` | `EPIC_BACKLIGHT_GPIO` | BCM pin driving the HAT backlight |
| — | — | `EPIC_NO_BACKLIGHT_CTL=1` | Skip `pinctrl`; black-frame only |

`SCREEN_ON` / `SCREEN_OFF` read their env overrides at module load, consistent
with how the rest of the settings block works.

## Components

### `_parse_clock(hhmm) -> datetime.time` (pure)

Parse `'HH:MM'` (24h) into a `datetime.time`. Tolerant: surrounding whitespace
trimmed. Raises `ValueError` on malformed input (caller passes known-good
constants, so this just guards typos in env overrides).

### `is_screen_on(now, on_time, off_time) -> bool` (pure)

Given the current `datetime` and two `datetime.time` boundaries, return whether
the screen should be on.

- **Non-wrap** (`on_time < off_time`, e.g. 08:00 / 22:00): on when
  `on_time <= now.time() < off_time`.
- **Wrap** (`on_time > off_time`, e.g. 22:00 / 08:00 — "on overnight"): on when
  `now.time() >= on_time or now.time() < off_time`.
- **Degenerate** (`on_time == off_time`): always on.
- On-boundary inclusive, off-boundary exclusive (at exactly 22:00 the screen is
  off).

Minute precision (compares full `time`, not just hour).

### `_set_backlight(on) -> bool` (impure, best-effort)

Drive the backlight pin via `pinctrl`:

- on → `pinctrl set <BACKLIGHT_GPIO> op dh`
- off → `pinctrl set <BACKLIGHT_GPIO> op dl`

No-op returning `False` when `EPIC_NO_BACKLIGHT_CTL` is set. Swallows
`FileNotFoundError` (pinctrl absent) and non-zero exit, returning `False` so a
broken backlight path never crashes the app. Returns `True` only when the
command ran and exited 0. Used for logging and tests; the app does not depend on
the return value.

### Main-loop wiring

Compute `now_on = NIGHT_MODE ? is_screen_on(now, on_t, off_t) : True` each frame.
Track previous `screen_on` (init `True`). Act only on edges:

- **on → off:** `_set_backlight(False)`; reset `state` to `MODE_PHOTO` with
  `overlay_dismiss_at=None` (so a wake shows a photo, not a stale overlay); blit
  a cached black surface and `_present` it once.
- **off → on:** `_set_backlight(True)`; fall through to a normal render so the
  current photo repaints immediately.
- **while off:** drain events for QUIT / ESC only (no `tick_state`, no
  `_maybe_kick_tap_refresh`); skip photo + overlay rendering; `clock.tick(4)`
  instead of 30 to spare CPU. The background weather thread is untouched and
  keeps the cache warm for morning.

Startup during night: `screen_on` inits `True`, so the first loop iteration
detects the on→off edge and blanks correctly.

## Data Flow

```
main loop frame
  now = datetime.now()
  now_on = NIGHT_MODE and is_screen_on(now, on_t, off_t)  # or True if disabled
  if screen_on and not now_on:        # on -> off edge
      _set_backlight(False); state = photo+cleared overlay; paint black
  elif not screen_on and now_on:      # off -> on edge
      _set_backlight(True)            # then normal render below
  if now_on:
      ... existing tick_state + render path ...
      clock.tick(30)
  else:
      drain QUIT/ESC; clock.tick(4)
  screen_on = now_on
```

## Error Handling

- `pinctrl` missing / non-zero exit / OS error → caught in `_set_backlight`,
  logged once, returns `False`; black frame still guarantees a dark screen.
- Malformed `EPIC_SCREEN_ON` / `EPIC_SCREEN_OFF` → `_parse_clock` raises
  `ValueError` at startup (fail fast on a misconfigured schedule, before the
  loop). Documented in CLAUDE.md.

## Testing

New test classes in `test_epic.py`:

- `TestParseClock` — `'08:00'`, `'22:30'`, whitespace, invalid (`'25:00'`,
  `'abc'`, `''`) raises.
- `TestIsScreenOn` — non-wrap on/off, wrap on/off, on-boundary inclusive,
  off-boundary exclusive, minute precision, degenerate equal-times.
- `TestSetBacklight` — mock `subprocess.run`: on → `dh`, off → `dl`,
  `EPIC_NO_BACKLIGHT_CTL` skips and returns `False`, `FileNotFoundError`
  swallowed → `False`, non-zero exit → `False`, success → `True`, custom
  `BACKLIGHT_GPIO`.

Pure functions are exercised directly. `_set_backlight` is tested with a mocked
subprocess. The edge-triggered main-loop branch is thin wiring over these tested
units (consistent with the existing partially-covered `main()`); coverage stays
≥95%.

## Documentation

- CLAUDE.md: add the new settings to the constants list, add a "Night mode"
  subsection under behaviour, document the env vars and the `pinctrl` best-effort
  / black-frame-fallback stance.
- `is_screen_on`, `_set_backlight` referenced in the Code Architecture section's
  helper list.

## Out of Scope

- Sunrise/sunset-driven schedule (the weather cache already has sun times — a
  possible future enhancement, not this feature).
- Runtime brightness / PWM dimming (separate deferred `brightness.sh` task).
- Wake-on-tap during night (explicitly decided against).
