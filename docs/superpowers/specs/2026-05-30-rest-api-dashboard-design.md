# REST API + Dashboard for Home Assistant — Design

**Date:** 2026-05-30
**Status:** Approved, pending implementation
**Feature:** An HTTP control surface for the EPIC viewer — a small REST API plus
a button dashboard — so Home Assistant (and a browser) can toggle the weather
overlay, force the screen on/off, and force-load the latest EPIC image.

## Problem

The viewer currently exposes only local input (unreliable touch) and SIGUSR1
over SSH. We want to drive it from Home Assistant on the same LAN: show/hide
weather, override the day/night screen schedule, and pull the newest Earth image
on demand — plus a minimal web page with buttons for manual use.

## Constraints

- Runs on a Pi Zero W (armv6, 512 MB). **FastAPI is rejected**: pydantic-core is
  Rust with no armv6 wheels, so pip would build from source (Rust toolchain,
  slow/fragile) and it is the heaviest option at runtime.
- **No new third-party dependencies.** The API uses the standard library
  `http.server`. `requirements.txt` is unchanged.
- Render + state stay on the main thread (project convention). Only blocking
  I/O — here, the HTTP server — runs in a daemon thread.
- Keep state logic in pure, unit-testable functions. The HTTP layer must be
  testable without opening sockets.
- Single-file rule is relaxed for a strong reason: the web server + dashboard
  HTML live in a new module, `epic_api.py`.

## Decisions

1. **Transport = stdlib `http.server`.** Zero dependencies, lightest on the Pi
   Zero, adequate for a handful of endpoints and one HTML page.
2. **New module `epic_api.py`.** Isolates web routing + dashboard from the
   render loop. `epic.py` imports it; `epic_api.py` imports nothing from
   `epic.py` (no circular import).
3. **Bind `0.0.0.0`, no auth.** Trusted home LAN; HA reaches it directly.
   Configurable host/port; the server can be disabled entirely via env.
4. **Screen override is sticky.** A force on/off persists until changed;
   `auto` hands control back to the night schedule (feature 1).
5. **API overlay "show" has no auto-dismiss.** Unlike a tap/SIGUSR1 (60 s safety
   dismiss), an API show stays until an explicit hide — HA owns the lifecycle.

## Architecture

```
┌────────────────────────┐         ApiBridge          ┌─────────────────────┐
│  HTTP server thread     │  push_command ──▶ deque    │  main render loop    │
│  (http.server, daemon)  │  get_status   ◀── dict     │  (30 FPS / 4 FPS)    │
│  dispatch(method,path)  │                            │  drain_commands()    │
└────────────────────────┘  ◀── set_status(build_status)  apply + publish    │
                                                        └─────────────────────┘
```

- **Commands** (API → loop): lock-guarded `collections.deque`. The handler
  pushes a command dict; the main loop drains all pending commands once per
  frame and applies them.
- **Status** (loop → API): lock-guarded dict. The main loop publishes a fresh
  status snapshot each frame; `GET /api/status` returns the latest.

The `HTTPServer` instance carries the bridge as `server.bridge`; the handler
reads `self.server.bridge`. This is the standard stdlib way to inject
dependencies into `BaseHTTPRequestHandler` without constructor gymnastics.

## Components

### `epic_api.py` (new)

**`ApiBridge`** — the thread-safe seam.
- `push_command(cmd: dict)` — append under lock.
- `drain_commands() -> list[dict]` — pop all under lock, return in order.
- `set_status(status: dict)` — replace status under lock.
- `get_status() -> dict` — copy status under lock.

**`dispatch(method, path, status_provider) -> (code, content_type, body, command)`**
Pure router. `status_provider` is a zero-arg callable returning the status dict
(in production, `bridge.get_status`). Returns the HTTP status code, content
type, response body (`bytes`), and an optional command dict to enqueue.

- `GET /` → `200 text/html`, `DASHBOARD_HTML`, no command.
- `GET /api/status` → `200 application/json`, `json.dumps(status_provider())`,
  no command.
- `POST` to a known action path → `200 application/json`, `{"ok": true,
  "command": <cmd>}`, command = the mapped dict.
- Anything else → `404 application/json`, `{"ok": false, "error": "not found"}`,
  no command.

POST route table:

| Path | Command |
|---|---|
| `/api/overlay/show` | `{'cmd': 'overlay', 'action': 'show'}` |
| `/api/overlay/hide` | `{'cmd': 'overlay', 'action': 'hide'}` |
| `/api/overlay/toggle` | `{'cmd': 'overlay', 'action': 'toggle'}` |
| `/api/screen/on` | `{'cmd': 'screen', 'action': 'on'}` |
| `/api/screen/off` | `{'cmd': 'screen', 'action': 'off'}` |
| `/api/screen/auto` | `{'cmd': 'screen', 'action': 'auto'}` |
| `/api/image/refresh` | `{'cmd': 'refresh_image'}` |

**`DASHBOARD_HTML`** — one self-contained page (inline CSS + vanilla JS).
Sections: Weather (Show / Hide), Screen (On / Off / Auto), Image (Refresh
latest). A status line polls `GET /api/status` every few seconds and shows
screen state, mode, current temp/condition, and last image date. `fetch` POSTs
to the action endpoints.

**`start_api_server(bridge, host, port)`** — builds an `HTTPServer`, attaches
`server.bridge = bridge`, and serves forever on a daemon thread. The handler
reads the body length for POSTs (discarded — commands carry no payload), calls
`dispatch`, enqueues any returned command, and writes the response.

### New pure helpers in `epic.py` (all unit-tested)

- `resolve_screen_on(override, scheduled_on) -> bool`
  `'on'→True`, `'off'→False`, `'auto'→scheduled_on`.
- `apply_overlay_command(state, action, now) -> AppState`
  `'show'` → `MODE_OVERLAY`, `overlay_dismiss_at=None`;
  `'hide'` → `MODE_PHOTO`, `overlay_dismiss_at=None`;
  `'toggle'` → overlay↔photo (to overlay with no auto-dismiss).
- `build_status(state, weather, screen_on, override, on_t, off_t) -> dict`
  JSON-serializable: `screen_on`, `screen_override`, `night_mode`,
  `screen_on_time`, `screen_off_time`, `mode`, `num_photos`, `current_idx`,
  `last_image_date`, and a `weather` sub-dict (`temp_c`, `condition`,
  `wind_kmh`, `fetched_at` string) or `None` when the cache is empty.
- `_maybe_check_for_new_images(state, screen, now, force=False)` — `force`
  bypasses both the `next_image_api_check_at` time gate and the date-equality
  gate, re-downloading and displaying the newest image even if its date is
  unchanged. Default `force=False` keeps existing callers/tests working.

### Main-loop wiring (`epic.py`)

Setup before the loop: create `api_bridge = epic_api.ApiBridge()`, start the
server (unless `EPIC_API_DISABLE`), init `screen_override = 'auto'`,
`force_image_refresh = False`.

Each frame, after event handling and before the night branch:

```
for cmd in api_bridge.drain_commands():
    if cmd['cmd'] == 'overlay':
        state = apply_overlay_command(state, cmd['action'], now)
    elif cmd['cmd'] == 'screen':
        screen_override = cmd['action']
    elif cmd['cmd'] == 'refresh_image':
        force_image_refresh = True

scheduled_on = (not NIGHT_MODE) or is_screen_on(now, on_t, off_t)
now_on = resolve_screen_on(screen_override, scheduled_on)
# ... existing night edge branch using now_on ...
```

When awake, image check uses and clears the flag:

```
state = _maybe_check_for_new_images(state, screen, now, force=force_image_refresh)
force_image_refresh = False
```

And once per frame publish status:

```
with weather_lock:
    weather_snapshot = weather_cache_ref.get('value')
api_bridge.set_status(build_status(state, weather_snapshot, now_on,
                                   screen_override, on_t, off_t))
```

## Data Flow

1. HA POSTs `/api/screen/off` → handler → `dispatch` returns a command →
   `bridge.push_command`.
2. Next frame, main loop `drain_commands` → `screen_override = 'off'` →
   `resolve_screen_on('off', …) = False` → night edge fires `'sleep'` →
   backlight off + black frame.
3. HA GETs `/api/status` → handler → `dispatch` → `status_provider()` →
   latest `build_status` snapshot → JSON with `screen_on=false`,
   `screen_override='off'`.

## Error Handling

- Unknown path / method → `404` JSON. Never raises.
- `dispatch` is total (always returns a tuple); the handler wraps the socket
  write so a broken client connection can't crash the server thread.
- `force_image_refresh` network/API errors are already handled inside
  `_maybe_check_for_new_images` (logged, schedule advanced); a forced refresh
  that fails simply leaves the current images in place.
- The server thread is a daemon — it never blocks shutdown. If the port is in
  use, `start_api_server` logs and the app continues without the API rather than
  crashing the display.

## Home Assistant integration (reference)

`rest_command` for the buttons:

```yaml
rest_command:
  epic_screen_off:
    url: "http://<pi-ip>:8080/api/screen/off"
    method: POST
  epic_screen_on:
    url: "http://<pi-ip>:8080/api/screen/on"
    method: POST
  epic_screen_auto:
    url: "http://<pi-ip>:8080/api/screen/auto"
    method: POST
  epic_weather_show:
    url: "http://<pi-ip>:8080/api/overlay/show"
    method: POST
  epic_weather_hide:
    url: "http://<pi-ip>:8080/api/overlay/hide"
    method: POST
  epic_image_refresh:
    url: "http://<pi-ip>:8080/api/image/refresh"
    method: POST
```

REST sensor for state:

```yaml
sensor:
  - platform: rest
    name: EPIC Viewer
    resource: "http://<pi-ip>:8080/api/status"
    value_template: "{{ 'on' if value_json.screen_on else 'off' }}"
    json_attributes:
      - screen_override
      - mode
      - last_image_date
      - weather
    scan_interval: 30
```

## Configuration

| Constant | Default | Env override | Meaning |
|---|---|---|---|
| `API_HOST` | `'0.0.0.0'` | `EPIC_API_HOST` | Bind address |
| `API_PORT` | `8080` | `EPIC_API_PORT` | Listen port |
| — | — | `EPIC_API_DISABLE=1` | Don't start the server |

## Testing

New tests in `test_epic.py` (imports `epic_api`):

- `TestResolveScreenOn` — on / off / auto.
- `TestApplyOverlayCommand` — show, hide, toggle from photo and overlay modes;
  no auto-dismiss set.
- `TestBuildStatus` — populated weather, empty cache (`weather: None`), field
  presence, JSON-serializable (`json.dumps` round-trips).
- `TestForceImageRefresh` — `force=True` bypasses the time gate and re-downloads
  on an unchanged date (mock `get_epic_images_json` + `save_photos`);
  `force=False` still respects the gate.
- `TestApiBridge` — push/drain order, drain empties, set/get status copy
  semantics.
- `TestDispatch` — `GET /` HTML, `GET /api/status` returns provider JSON, each
  POST route maps to its command + `200`, unknown path → `404`, GET on a POST
  route → `404`.

All socket-free and thread-free. The daemon server thread and `start_api_server`
socket binding are not unit-tested (consistent with the untested I/O layer);
overall line coverage is reported but the new *logic* is fully covered.

## Out of Scope

- Authentication / TLS (trusted LAN; revisit if exposed beyond it).
- Forcing a weather refresh over HTTP (the 30-min thread + tap refresh already
  cover it; not requested).
- Per-photo selection / arbitrary image navigation (only "newest" is requested).
- WebSocket / push updates (HA polls `/api/status`).
