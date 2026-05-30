# REST API + Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose an HTTP REST API + button dashboard so Home Assistant and a browser can show/hide the weather overlay, force the screen on/off, and force-load the newest EPIC image.

**Architecture:** A stdlib `http.server` runs in a daemon thread. It talks to the pygame main loop through a thread-safe `ApiBridge` (command deque in, status dict out). All `AppState` mutation happens on the main thread via pure helpers. New web code lives in `epic_api.py`; `epic.py` imports it.

**Tech Stack:** Python 3.9+, stdlib `http.server` / `json` / `threading` / `collections` (no new deps), pytest + unittest.mock.

---

### Task 1: `ApiBridge` (thread-safe seam)

**Files:** Create `epic_api.py`; Test `test_epic.py`.

- [ ] **Step 1: Write failing tests** (new class in `test_epic.py`, add `import epic_api` near the top imports):

```python
class TestApiBridge:
    def test_push_drain_order(self):
        b = epic_api.ApiBridge()
        b.push_command({'cmd': 'a'})
        b.push_command({'cmd': 'b'})
        assert b.drain_commands() == [{'cmd': 'a'}, {'cmd': 'b'}]

    def test_drain_empties(self):
        b = epic_api.ApiBridge()
        b.push_command({'cmd': 'a'})
        b.drain_commands()
        assert b.drain_commands() == []

    def test_status_roundtrip_is_copy(self):
        b = epic_api.ApiBridge()
        b.set_status({'x': 1})
        got = b.get_status()
        assert got == {'x': 1}
        got['x'] = 2
        assert b.get_status() == {'x': 1}
```

- [ ] **Step 2: Run** `pytest test_epic.py::TestApiBridge -q` → FAIL (no module).

- [ ] **Step 3: Implement** `epic_api.py`:

```python
import collections
import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer


class ApiBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._commands = collections.deque()
        self._status = {}

    def push_command(self, cmd):
        with self._lock:
            self._commands.append(cmd)

    def drain_commands(self):
        with self._lock:
            drained = list(self._commands)
            self._commands.clear()
        return drained

    def set_status(self, status):
        with self._lock:
            self._status = dict(status)

    def get_status(self):
        with self._lock:
            return dict(self._status)
```

- [ ] **Step 4: Run** `pytest test_epic.py::TestApiBridge -q` → PASS.

- [ ] **Step 5: Commit** `feat(api): add thread-safe ApiBridge`.

---

### Task 2: `dispatch` router + dashboard HTML + server starter

**Files:** Modify `epic_api.py`; Test `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestDispatch:
    def _provider(self):
        return lambda: {'screen_on': True, 'mode': 'photo'}

    def test_root_html(self):
        code, ctype, body, cmd = epic_api.dispatch('GET', '/', self._provider())
        assert code == 200
        assert 'text/html' in ctype
        assert b'<html' in body.lower() if isinstance(body, bytes) else True
        assert cmd is None

    def test_status_json(self):
        code, ctype, body, cmd = epic_api.dispatch('GET', '/api/status', self._provider())
        assert code == 200
        assert 'application/json' in ctype
        assert json.loads(body) == {'screen_on': True, 'mode': 'photo'}
        assert cmd is None

    def test_overlay_show(self):
        code, _, body, cmd = epic_api.dispatch('POST', '/api/overlay/show', self._provider())
        assert code == 200
        assert cmd == {'cmd': 'overlay', 'action': 'show'}
        assert json.loads(body)['ok'] is True

    def test_screen_off(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/screen/off', self._provider())
        assert cmd == {'cmd': 'screen', 'action': 'off'}

    def test_image_refresh(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/image/refresh', self._provider())
        assert cmd == {'cmd': 'refresh_image'}

    def test_unknown_path_404(self):
        code, _, body, cmd = epic_api.dispatch('GET', '/nope', self._provider())
        assert code == 404
        assert cmd is None
        assert json.loads(body)['ok'] is False

    def test_get_on_post_route_404(self):
        code, _, _, cmd = epic_api.dispatch('GET', '/api/screen/off', self._provider())
        assert code == 404
        assert cmd is None
```

- [ ] **Step 2: Run** `pytest test_epic.py::TestDispatch -q` → FAIL.

- [ ] **Step 3: Implement** in `epic_api.py` (append):

```python
_POST_ROUTES = {
    '/api/overlay/show': {'cmd': 'overlay', 'action': 'show'},
    '/api/overlay/hide': {'cmd': 'overlay', 'action': 'hide'},
    '/api/overlay/toggle': {'cmd': 'overlay', 'action': 'toggle'},
    '/api/screen/on': {'cmd': 'screen', 'action': 'on'},
    '/api/screen/off': {'cmd': 'screen', 'action': 'off'},
    '/api/screen/auto': {'cmd': 'screen', 'action': 'auto'},
    '/api/image/refresh': {'cmd': 'refresh_image'},
}


def dispatch(method, path, status_provider):
    if method == 'GET' and path == '/':
        return 200, 'text/html; charset=utf-8', DASHBOARD_HTML.encode('utf-8'), None
    if method == 'GET' and path == '/api/status':
        body = json.dumps(status_provider()).encode('utf-8')
        return 200, 'application/json', body, None
    if method == 'POST' and path in _POST_ROUTES:
        cmd = _POST_ROUTES[path]
        body = json.dumps({'ok': True, 'command': cmd}).encode('utf-8')
        return 200, 'application/json', body, cmd
    body = json.dumps({'ok': False, 'error': 'not found'}).encode('utf-8')
    return 404, 'application/json', body, None


class _Handler(BaseHTTPRequestHandler):
    def _respond(self, method):
        code, ctype, body, cmd = dispatch(method, self.path, self.server.bridge.get_status)
        if cmd is not None:
            self.server.bridge.push_command(cmd)
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._respond('GET')

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        self._respond('POST')

    def log_message(self, *args):
        pass


def start_api_server(bridge, host, port):
    try:
        server = HTTPServer((host, port), _Handler)
    except OSError as exc:
        print('API server not started:', exc)
        return None
    server.bridge = bridge
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print('API server on http://' + host + ':' + str(port))
    return server
```

- [ ] **Step 4: Add `DASHBOARD_HTML`** near the top of `epic_api.py` (after imports):

```python
DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EPIC Viewer</title>
<style>
 body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:1.2rem;max-width:520px;margin:auto}
 h1{font-size:1.2rem;font-weight:600}
 section{margin:1rem 0;padding:1rem;background:#1c1c1c;border-radius:12px}
 h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:#999;margin:0 0 .6rem}
 button{font-size:1rem;padding:.7rem 1.1rem;margin:.25rem;border:0;border-radius:10px;background:#2d6cdf;color:#fff}
 button.alt{background:#444}
 #status{font-size:.85rem;color:#bbb;white-space:pre-line;line-height:1.5}
</style></head><body>
<h1>DSCOVR:EPIC Viewer</h1>
<section><h2>Weather overlay</h2>
 <button onclick="post('/api/overlay/show')">Show</button>
 <button class="alt" onclick="post('/api/overlay/hide')">Hide</button></section>
<section><h2>Screen</h2>
 <button onclick="post('/api/screen/on')">On</button>
 <button class="alt" onclick="post('/api/screen/off')">Off</button>
 <button class="alt" onclick="post('/api/screen/auto')">Auto (schedule)</button></section>
<section><h2>Image</h2>
 <button onclick="post('/api/image/refresh')">Load latest EPIC image</button></section>
<section><h2>Status</h2><div id="status">…</div></section>
<script>
 async function post(p){await fetch(p,{method:'POST'});setTimeout(refresh,400);}
 async function refresh(){
  try{const r=await fetch('/api/status');const s=await r.json();
   const w=s.weather?`${s.weather.temp_c}°C ${s.weather.condition}`:'—';
   document.getElementById('status').textContent=
    `screen: ${s.screen_on?'ON':'OFF'} (${s.screen_override})\\nmode: ${s.mode}\\nweather: ${w}\\nlast image: ${s.last_image_date||'—'}`;
  }catch(e){document.getElementById('status').textContent='offline';}
 }
 refresh();setInterval(refresh,5000);
</script></body></html>"""
```

- [ ] **Step 5: Run** `pytest test_epic.py::TestDispatch -q` → PASS.

- [ ] **Step 6: Commit** `feat(api): add dispatch router, dashboard HTML, server starter`.

---

### Task 3: Pure helpers in `epic.py`

**Files:** Modify `epic.py`; Test `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestResolveScreenOn:
    def test_on(self):
        assert epic.resolve_screen_on('on', False) is True

    def test_off(self):
        assert epic.resolve_screen_on('off', True) is False

    def test_auto_follows_schedule(self):
        assert epic.resolve_screen_on('auto', True) is True
        assert epic.resolve_screen_on('auto', False) is False


class TestApplyOverlayCommand:
    def _state(self, mode):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        return epic.AppState(mode=mode, current_idx=0, num_photos=3,
                             next_photo_swap_at=now, next_image_api_check_at=now,
                             overlay_dismiss_at=None, blend_started_at=None,
                             last_image_data='x')

    def test_show(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        s = epic.apply_overlay_command(self._state(epic.MODE_PHOTO), 'show', now)
        assert s.mode == epic.MODE_OVERLAY
        assert s.overlay_dismiss_at is None

    def test_hide(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        s = epic.apply_overlay_command(self._state(epic.MODE_OVERLAY), 'hide', now)
        assert s.mode == epic.MODE_PHOTO

    def test_toggle_from_photo(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        s = epic.apply_overlay_command(self._state(epic.MODE_PHOTO), 'toggle', now)
        assert s.mode == epic.MODE_OVERLAY

    def test_toggle_from_overlay(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        s = epic.apply_overlay_command(self._state(epic.MODE_OVERLAY), 'toggle', now)
        assert s.mode == epic.MODE_PHOTO


class TestBuildStatus:
    def _state(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        return epic.AppState(mode=epic.MODE_PHOTO, current_idx=1, num_photos=4,
                             next_photo_swap_at=now, next_image_api_check_at=now,
                             overlay_dismiss_at=None, blend_started_at=None,
                             last_image_data='2026-05-30 09:00:00')

    def test_with_weather_is_json(self):
        weather = {'temp_c': 17, 'condition': 'Rain', 'wind_kmh': 9,
                   'fetched_at': datetime.datetime(2026, 5, 30, 11, 30)}
        st = epic.build_status(self._state(), weather, True, 'auto',
                               datetime.time(8, 0), datetime.time(22, 0))
        assert st['screen_on'] is True
        assert st['screen_override'] == 'auto'
        assert st['weather']['temp_c'] == 17
        assert st['last_image_date'] == '2026-05-30 09:00:00'
        json.dumps(st)  # must not raise

    def test_without_weather(self):
        st = epic.build_status(self._state(), None, False, 'off',
                               datetime.time(8, 0), datetime.time(22, 0))
        assert st['weather'] is None
        assert st['screen_on'] is False
        json.dumps(st)
```

- [ ] **Step 2: Run** the three classes → FAIL.

- [ ] **Step 3: Implement** in `epic.py` after `night_transition`:

```python
def resolve_screen_on(override, scheduled_on):
    if override == 'on':
        return True
    if override == 'off':
        return False
    return scheduled_on


def apply_overlay_command(state, action, now):
    if action == 'show':
        return replace(state, mode=MODE_OVERLAY, overlay_dismiss_at=None)
    if action == 'hide':
        return replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
    if state.mode == MODE_OVERLAY:
        return replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
    return replace(state, mode=MODE_OVERLAY, overlay_dismiss_at=None)


def build_status(state, weather, screen_on, override, on_time, off_time):
    weather_out = None
    if weather:
        fetched = weather.get('fetched_at')
        weather_out = {
            'temp_c': weather.get('temp_c'),
            'condition': weather.get('condition'),
            'wind_kmh': weather.get('wind_kmh'),
            'fetched_at': fetched.strftime('%Y-%m-%d %H:%M') if fetched else None,
        }
    return {
        'screen_on': screen_on,
        'screen_override': override,
        'night_mode': NIGHT_MODE,
        'screen_on_time': on_time.strftime('%H:%M'),
        'screen_off_time': off_time.strftime('%H:%M'),
        'mode': state.mode,
        'num_photos': state.num_photos,
        'current_idx': state.current_idx,
        'last_image_date': state.last_image_data,
        'weather': weather_out,
    }
```

- [ ] **Step 4: Run** the three classes → PASS.

- [ ] **Step 5: Commit** `feat(api): add resolve_screen_on / apply_overlay_command / build_status`.

---

### Task 4: `force` param on `_maybe_check_for_new_images`

**Files:** Modify `epic.py`; Test `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestForceImageRefresh:
    def _state(self, last):
        future = datetime.datetime(2030, 1, 1)
        return epic.AppState(mode=epic.MODE_PHOTO, current_idx=0, num_photos=1,
                             next_photo_swap_at=future, next_image_api_check_at=future,
                             overlay_dismiss_at=None, blend_started_at=None,
                             last_image_data=last)

    @mock.patch('epic.save_photos')
    @mock.patch('epic.get_epic_images_json')
    def test_force_redownloads_same_date(self, mock_json, mock_save):
        mock_json.return_value = [{'date': '2026-05-30 09:00:00', 'image': 'img'}]
        now = datetime.datetime(2026, 5, 30, 10, 0)
        state = self._state('2026-05-30 09:00:00')  # same date already shown
        out = epic._maybe_check_for_new_images(state, None, now, force=True)
        mock_save.assert_called_once()
        assert out.num_photos == 1

    @mock.patch('epic.save_photos')
    @mock.patch('epic.get_epic_images_json')
    def test_no_force_respects_time_gate(self, mock_json, mock_save):
        now = datetime.datetime(2026, 5, 30, 10, 0)
        state = self._state('old')  # next check far in the future
        epic._maybe_check_for_new_images(state, None, now, force=False)
        mock_json.assert_not_called()
        mock_save.assert_not_called()
```

- [ ] **Step 2: Run** → FAIL (unexpected `force` kwarg / wrong behavior).

- [ ] **Step 3: Edit** `_maybe_check_for_new_images` signature + gates:

Change `def _maybe_check_for_new_images(state, screen, now):` to
`def _maybe_check_for_new_images(state, screen, now, force=False):`.
Change `if now < state.next_image_api_check_at:` to
`if not force and now < state.next_image_api_check_at:`.
Change `if newest and newest != state.last_image_data:` to
`if newest and (force or newest != state.last_image_data):`.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(api): force flag on _maybe_check_for_new_images`.

---

### Task 5: Wire the API into `main()`

**Files:** Modify `epic.py`.

- [ ] **Step 1: Add `import epic_api`** to the third-party/local import block (after `import requests`).

- [ ] **Step 2: Add config constants** after the night-mode block:

```python
API_HOST = os.environ.get('EPIC_API_HOST', '0.0.0.0')
API_PORT = int(os.environ.get('EPIC_API_PORT', '8080'))
```

- [ ] **Step 3: Setup before the loop** (next to the night-mode setup):

```python
    api_bridge = epic_api.ApiBridge()
    if not os.environ.get('EPIC_API_DISABLE'):
        epic_api.start_api_server(api_bridge, API_HOST, API_PORT)
    screen_override = 'auto'
    force_image_refresh = False
```

- [ ] **Step 4: Drain + apply commands** at the top of the loop body, right after the QUIT/ESC `if not running: break`:

```python
        for cmd in api_bridge.drain_commands():
            if cmd['cmd'] == 'overlay':
                state = apply_overlay_command(state, cmd['action'], now)
            elif cmd['cmd'] == 'screen':
                screen_override = cmd['action']
            elif cmd['cmd'] == 'refresh_image':
                force_image_refresh = True
```

- [ ] **Step 5: Replace** the night `now_on` line:

```python
        scheduled_on = (not NIGHT_MODE) or is_screen_on(now, on_t, off_t)
        now_on = resolve_screen_on(screen_override, scheduled_on)
```

- [ ] **Step 6: Publish status** just before the night edge `if not now_on` idle branch publishes, i.e. immediately after `screen_on = now_on`:

```python
        with weather_lock:
            _weather_snapshot = weather_cache_ref.get('value')
        api_bridge.set_status(
            build_status(state, _weather_snapshot, now_on, screen_override, on_t, off_t)
        )
```

- [ ] **Step 7: Pass the force flag** to the image check and clear it:

Change `state = _maybe_check_for_new_images(state, screen, now)` to:

```python
        state = _maybe_check_for_new_images(state, screen, now, force=force_image_refresh)
        force_image_refresh = False
```

- [ ] **Step 8: Sanity-parse + full suite** `python -c "import ast; ast.parse(open('epic.py', encoding='utf-8').read()); print('OK')"` then `pytest -q` → all PASS.

- [ ] **Step 9: Commit** `feat(api): wire REST control surface into main loop`.

---

### Task 6: Format, repo structure, docs

**Files:** Modify `CLAUDE.md`, `requirements.txt` (verify unchanged).

- [ ] **Step 1: Format** `isort epic.py epic_api.py test_epic.py --sp=.isort.cfg ; black epic.py epic_api.py test_epic.py --config=pyproject.toml`.

- [ ] **Step 2: Full suite + coverage** `pytest -q` PASS; `pytest --cov=epic --cov=epic_api --cov-report=term-missing -q`.

- [ ] **Step 3: Update CLAUDE.md** — add `epic_api.py` to the repo structure + tech stack; add a "REST API / dashboard" architecture subsection (bridge, endpoints, env, HA snippet pointer); note new constants `API_HOST`/`API_PORT`.

- [ ] **Step 4: Commit** `docs: document REST API + dashboard`.
