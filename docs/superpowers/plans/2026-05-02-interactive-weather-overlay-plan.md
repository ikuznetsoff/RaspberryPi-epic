# Interactive Weather Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tap-toggled, full-screen, dimmed information overlay to the EPIC photo
viewer that displays a live clock and current/forecast weather (temperature, condition,
sunrise/sunset, today + tomorrow rain) for a configured city, while preserving the
single-file architecture and adding zero new third-party dependencies.

**Architecture:** Refactor `epic.py` from blocking `time.sleep` loops to a 30 FPS
frame-tick event loop driven by `pygame.time.Clock` and `datetime` checkpoints. Add a
pure `tick_state` state-machine function (`PHOTO`/`BLENDING`/`OVERLAY`), pure render
helpers, and an Open-Meteo weather client. Background daemon thread refreshes weather
every 30 min; tap also triggers a one-shot fetch if cache is older than 10 min.

**Tech Stack:** Python 3.9+, pygame 2.6.1, requests 2.32.5, threading (stdlib),
dataclasses (stdlib). No new deps.

**Spec:** `docs/superpowers/specs/2026-05-02-interactive-weather-overlay-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `epic.py` | Modify | Adds config, WMO mapping, geocoding, weather fetch, cache, state dataclass, `tick_state`, `render_overlay`, refactors event loop. Stays single-file. |
| `test_epic.py` | Modify | Adds test classes for new functions. Existing 45 tests stay green; new tests bring total ≥ 60. |
| `requirements.txt` | No change | No new dependencies. |
| `docs/superpowers/specs/2026-05-02-interactive-weather-overlay-design.md` | Reference | Source of truth for design decisions. |

All new code lives in `epic.py`. All new tests live in `test_epic.py`. Resist the
temptation to split.

---

## Task 1: Config constants + WMO code mapping

**Files:**
- Modify: `epic.py` (top-of-file settings block + new module dict)
- Test: `test_epic.py` (new test classes)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# Config constants — weather overlay
# ============================================================


class TestWeatherConfig:
    def test_city_name_default(self):
        assert epic.CITY_NAME == 'Warsaw'

    def test_weather_refresh_minutes(self):
        assert epic.WEATHER_REFRESH_MIN == 30

    def test_weather_tap_refresh_minutes(self):
        assert epic.WEATHER_TAP_REFRESH_MIN == 10

    def test_http_timeout(self):
        assert epic.HTTP_TIMEOUT == 10

    def test_overlay_auto_dismiss(self):
        assert epic.OVERLAY_AUTO_DISMISS_SEC == 60


# ============================================================
# WMO weather code mapping
# ============================================================


class TestWeatherCodeMapping:
    @pytest.mark.parametrize(
        'code,expected',
        [
            (0, 'Clear'),
            (1, 'Mostly Clear'),
            (2, 'Partly Cloudy'),
            (3, 'Overcast'),
            (45, 'Fog'),
            (51, 'Light Drizzle'),
            (61, 'Light Rain'),
            (71, 'Light Snow'),
            (95, 'Thunderstorm'),
        ],
    )
    def test_known_codes(self, code, expected):
        assert epic.weather_code_to_text(code) == expected

    def test_unknown_code_returns_string(self):
        assert epic.weather_code_to_text(9999) == '9999'

    def test_none_returns_dash(self):
        assert epic.weather_code_to_text(None) == '—'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestWeatherConfig test_epic.py::TestWeatherCodeMapping -v`
Expected: All FAIL with `AttributeError: module 'epic' has no attribute 'CITY_NAME'` (and similar).

- [ ] **Step 3: Add config + WMO map + helper to `epic.py`**

In `epic.py`, immediately after the existing settings block (after `blending_duration = 5` line), insert:

```python
# Weather overlay settings
CITY_NAME = 'Warsaw'
WEATHER_REFRESH_MIN = 30
WEATHER_TAP_REFRESH_MIN = 10
HTTP_TIMEOUT = 10
OVERLAY_AUTO_DISMISS_SEC = 60
```

Then, after the existing `DISPLAY_SIZE`, `CROP_SIZE`, `CROP_OFFSET` constants, add:

```python
WMO_CODES = {
    0: 'Clear',
    1: 'Mostly Clear',
    2: 'Partly Cloudy',
    3: 'Overcast',
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


def weather_code_to_text(code):
    if code is None:
        return '—'
    return WMO_CODES.get(code, str(code))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestWeatherConfig test_epic.py::TestWeatherCodeMapping -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add weather overlay config + WMO code mapping"
```

---

## Task 2: `geocode_city` — happy path + error paths

**Files:**
- Modify: `epic.py` (new function)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# geocode_city
# ============================================================


class TestGeocodeCity:
    def test_happy_path(self):
        fake_response = mock.Mock()
        fake_response.json.return_value = {
            'results': [
                {
                    'name': 'Warsaw',
                    'latitude': 52.23,
                    'longitude': 21.01,
                    'country': 'Poland',
                }
            ]
        }
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response) as get:
            lat, lon, display = epic.geocode_city('Warsaw')
        assert lat == 52.23
        assert lon == 21.01
        assert display == 'Warsaw'
        get.assert_called_once()
        url, kwargs = get.call_args.args[0], get.call_args.kwargs
        assert 'geocoding-api.open-meteo.com' in url
        assert kwargs.get('timeout') == epic.HTTP_TIMEOUT

    def test_city_not_found_raises(self):
        fake_response = mock.Mock()
        fake_response.json.return_value = {'results': []}
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            with pytest.raises(LookupError, match='not found'):
                epic.geocode_city('Atlantis')

    def test_missing_results_key_raises(self):
        fake_response = mock.Mock()
        fake_response.json.return_value = {}
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            with pytest.raises(LookupError):
                epic.geocode_city('Atlantis')

    def test_http_failure_propagates(self):
        import requests as rq

        with mock.patch('epic.requests.get', side_effect=rq.Timeout('slow')):
            with pytest.raises(rq.Timeout):
                epic.geocode_city('Warsaw')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestGeocodeCity -v`
Expected: FAIL with `AttributeError: module 'epic' has no attribute 'geocode_city'`.

- [ ] **Step 3: Implement `geocode_city` in `epic.py`**

After the `weather_code_to_text` function, add:

```python
def geocode_city(name):
    response = requests.get(
        'https://geocoding-api.open-meteo.com/v1/search',
        params={'name': name, 'count': 1},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    results = data.get('results') or []
    if not results:
        raise LookupError("City '" + str(name) + "' not found via Open-Meteo geocoding")
    hit = results[0]
    return float(hit['latitude']), float(hit['longitude']), hit.get('name', name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestGeocodeCity -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add geocode_city function (Open-Meteo)"
```

---

## Task 3: `fetch_weather` — happy path, missing fields, timeout

**Files:**
- Modify: `epic.py` (new function)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# fetch_weather
# ============================================================


class TestFetchWeather:
    def _payload(self, **overrides):
        base = {
            'current': {
                'temperature_2m': -7.6,
                'weather_code': 2,
            },
            'daily': {
                'sunrise': ['2026-05-02T06:42', '2026-05-03T06:40'],
                'sunset': ['2026-05-02T19:08', '2026-05-03T19:10'],
                'precipitation_probability_max': [60, 80],
                'precipitation_sum': [2.0, 5.4],
            },
        }
        for k, v in overrides.items():
            base[k] = v
        return base

    def test_happy_path(self):
        fake_response = mock.Mock()
        fake_response.json.return_value = self._payload()
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response) as get:
            cache = epic.fetch_weather(52.23, 21.01)
        assert cache['temp_c'] == -8  # rounded
        assert cache['weather_code'] == 2
        assert cache['condition'] == 'Partly Cloudy'
        assert cache['sunrise'] == '06:42'
        assert cache['sunset'] == '19:08'
        assert cache['rain_today'] == (60, 2.0)
        assert cache['rain_tomorrow'] == (80, 5.4)
        assert isinstance(cache['fetched_at'], datetime.datetime)
        # Verify URL + params
        kwargs = get.call_args.kwargs
        assert kwargs['timeout'] == epic.HTTP_TIMEOUT
        assert kwargs['params']['latitude'] == 52.23
        assert kwargs['params']['longitude'] == 21.01
        assert kwargs['params']['forecast_days'] == 2

    def test_missing_precip_probability(self):
        payload = self._payload()
        payload['daily']['precipitation_probability_max'] = [None, None]
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['rain_today'] == (None, 2.0)
        assert cache['rain_tomorrow'] == (None, 5.4)

    def test_missing_precip_sum(self):
        payload = self._payload()
        payload['daily']['precipitation_sum'] = [None, None]
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['rain_today'] == (60, None)
        assert cache['rain_tomorrow'] == (80, None)

    def test_only_one_forecast_day(self):
        payload = self._payload()
        payload['daily']['precipitation_probability_max'] = [60]
        payload['daily']['precipitation_sum'] = [2.0]
        payload['daily']['sunrise'] = ['2026-05-02T06:42']
        payload['daily']['sunset'] = ['2026-05-02T19:08']
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['rain_today'] == (60, 2.0)
        assert cache['rain_tomorrow'] == (None, None)

    def test_timeout_propagates(self):
        import requests as rq

        with mock.patch('epic.requests.get', side_effect=rq.Timeout('slow')):
            with pytest.raises(rq.Timeout):
                epic.fetch_weather(0.0, 0.0)

    def test_unknown_weather_code(self):
        payload = self._payload()
        payload['current']['weather_code'] = 9999
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['condition'] == '9999'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestFetchWeather -v`
Expected: FAIL — `fetch_weather` not defined.

- [ ] **Step 3: Implement `fetch_weather` in `epic.py`**

After `geocode_city`, add:

```python
def _safe_index(seq, idx):
    if seq is None:
        return None
    if idx >= len(seq):
        return None
    return seq[idx]


def _parse_hhmm(iso_string):
    if iso_string is None:
        return None
    # Open-Meteo daily sunrise/sunset format: "YYYY-MM-DDTHH:MM"
    return iso_string.split('T', 1)[1][:5] if 'T' in iso_string else iso_string


def fetch_weather(lat, lon):
    response = requests.get(
        'https://api.open-meteo.com/v1/forecast',
        params={
            'latitude': lat,
            'longitude': lon,
            'current': 'temperature_2m,weather_code',
            'daily': 'sunrise,sunset,precipitation_probability_max,precipitation_sum',
            'forecast_days': 2,
            'timezone': 'auto',
            'temperature_unit': 'celsius',
        },
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    current = data.get('current', {})
    daily = data.get('daily', {})
    code = current.get('weather_code')
    temp = current.get('temperature_2m')
    sunrise_list = daily.get('sunrise', [])
    sunset_list = daily.get('sunset', [])
    prob_list = daily.get('precipitation_probability_max', [])
    sum_list = daily.get('precipitation_sum', [])
    return {
        'temp_c': int(round(temp)) if temp is not None else None,
        'weather_code': code,
        'condition': weather_code_to_text(code),
        'sunrise': _parse_hhmm(_safe_index(sunrise_list, 0)),
        'sunset': _parse_hhmm(_safe_index(sunset_list, 0)),
        'rain_today': (_safe_index(prob_list, 0), _safe_index(sum_list, 0)),
        'rain_tomorrow': (_safe_index(prob_list, 1), _safe_index(sum_list, 1)),
        'fetched_at': datetime.datetime.now(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestFetchWeather -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add fetch_weather (Open-Meteo current + 2-day forecast)"
```

---

## Task 4: Cache staleness helper

**Files:**
- Modify: `epic.py` (new helper)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# is_weather_stale
# ============================================================


class TestIsWeatherStale:
    def test_no_cache_is_stale(self):
        assert epic.is_weather_stale(None, refresh_min=30, now=datetime.datetime(2026, 5, 2, 12, 0)) is True

    def test_empty_dict_is_stale(self):
        assert epic.is_weather_stale({}, refresh_min=30, now=datetime.datetime(2026, 5, 2, 12, 0)) is True

    def test_fresh_cache_not_stale(self):
        cache = {'fetched_at': datetime.datetime(2026, 5, 2, 11, 50)}
        now = datetime.datetime(2026, 5, 2, 12, 0)
        assert epic.is_weather_stale(cache, refresh_min=30, now=now) is False

    def test_old_cache_is_stale(self):
        cache = {'fetched_at': datetime.datetime(2026, 5, 2, 11, 0)}
        now = datetime.datetime(2026, 5, 2, 12, 0)
        assert epic.is_weather_stale(cache, refresh_min=30, now=now) is True

    def test_exact_boundary_not_stale(self):
        cache = {'fetched_at': datetime.datetime(2026, 5, 2, 11, 30)}
        now = datetime.datetime(2026, 5, 2, 12, 0)
        # exactly 30 minutes — boundary is "still fresh"
        assert epic.is_weather_stale(cache, refresh_min=30, now=now) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestIsWeatherStale -v`
Expected: FAIL — `is_weather_stale` not defined.

- [ ] **Step 3: Implement `is_weather_stale` in `epic.py`**

After `fetch_weather`, add:

```python
def is_weather_stale(cache, refresh_min, now):
    if not cache:
        return True
    fetched_at = cache.get('fetched_at')
    if fetched_at is None:
        return True
    return (now - fetched_at) > datetime.timedelta(minutes=refresh_min)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestIsWeatherStale -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add is_weather_stale helper"
```

---

## Task 5: State dataclass + `tick_state` pure function

This task introduces the new state machine but does **not** wire it into `main()` yet.
That happens in Task 8 to keep diffs reviewable.

**Files:**
- Modify: `epic.py` (new dataclass + new function)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# AppState + tick_state
# ============================================================


class TestAppState:
    def _make_state(self, **overrides):
        defaults = {
            'mode': epic.MODE_PHOTO,
            'current_idx': 0,
            'num_photos': 5,
            'next_photo_swap_at': datetime.datetime(2026, 5, 2, 12, 0, 20),
            'next_image_api_check_at': datetime.datetime(2026, 5, 2, 14, 0, 0),
            'overlay_dismiss_at': None,
            'blend_started_at': None,
            'last_image_data': '',
        }
        defaults.update(overrides)
        return epic.AppState(**defaults)

    def test_modes_defined(self):
        assert epic.MODE_PHOTO == 'photo'
        assert epic.MODE_BLENDING == 'blending'
        assert epic.MODE_OVERLAY == 'overlay'

    def test_photo_to_overlay_on_tap(self):
        state = self._make_state()
        events = [mock.Mock(type=pygame.MOUSEBUTTONDOWN)]
        now = datetime.datetime(2026, 5, 2, 12, 0, 5)
        new = epic.tick_state(state, events, now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_OVERLAY
        assert new.overlay_dismiss_at == now + datetime.timedelta(seconds=epic.OVERLAY_AUTO_DISMISS_SEC)

    def test_overlay_to_photo_on_tap(self):
        now = datetime.datetime(2026, 5, 2, 12, 0, 5)
        state = self._make_state(
            mode=epic.MODE_OVERLAY,
            overlay_dismiss_at=now + datetime.timedelta(seconds=60),
        )
        events = [mock.Mock(type=pygame.MOUSEBUTTONDOWN)]
        new = epic.tick_state(state, events, now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_PHOTO
        assert new.overlay_dismiss_at is None

    def test_overlay_auto_dismiss(self):
        opened = datetime.datetime(2026, 5, 2, 12, 0, 0)
        state = self._make_state(
            mode=epic.MODE_OVERLAY,
            overlay_dismiss_at=opened + datetime.timedelta(seconds=60),
        )
        now = opened + datetime.timedelta(seconds=61)
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_PHOTO
        assert new.overlay_dismiss_at is None

    def test_photo_swap_starts_blend(self):
        now = datetime.datetime(2026, 5, 2, 12, 0, 25)
        state = self._make_state(
            current_idx=1,
            next_photo_swap_at=datetime.datetime(2026, 5, 2, 12, 0, 20),
        )
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_BLENDING
        assert new.blend_started_at == now

    def test_photo_swap_skips_blend_when_disabled(self):
        now = datetime.datetime(2026, 5, 2, 12, 0, 25)
        state = self._make_state(
            current_idx=1,
            next_photo_swap_at=datetime.datetime(2026, 5, 2, 12, 0, 20),
        )
        new = epic.tick_state(state, [], now, blend_enabled=False, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_PHOTO
        assert new.current_idx == 2
        assert new.next_photo_swap_at == now + datetime.timedelta(seconds=20)

    def test_blend_completes(self):
        started = datetime.datetime(2026, 5, 2, 12, 0, 25)
        now = started + datetime.timedelta(seconds=5, milliseconds=10)
        state = self._make_state(
            mode=epic.MODE_BLENDING,
            current_idx=1,
            blend_started_at=started,
        )
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_PHOTO
        assert new.current_idx == 2
        assert new.blend_started_at is None
        assert new.next_photo_swap_at == now + datetime.timedelta(seconds=20)

    def test_blend_wraps_at_end(self):
        started = datetime.datetime(2026, 5, 2, 12, 0, 25)
        now = started + datetime.timedelta(seconds=6)
        state = self._make_state(
            mode=epic.MODE_BLENDING,
            current_idx=4,
            num_photos=5,
            blend_started_at=started,
        )
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.current_idx == 0  # wrapped

    def test_tap_during_blend_finishes_blend_and_opens_overlay(self):
        started = datetime.datetime(2026, 5, 2, 12, 0, 25)
        now = started + datetime.timedelta(seconds=2)
        state = self._make_state(
            mode=epic.MODE_BLENDING,
            current_idx=1,
            blend_started_at=started,
        )
        events = [mock.Mock(type=pygame.MOUSEBUTTONDOWN)]
        new = epic.tick_state(state, events, now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_OVERLAY
        assert new.current_idx == 2  # blend completed
        assert new.blend_started_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestAppState -v`
Expected: FAIL — `AppState`, `MODE_PHOTO`, `tick_state` not defined.

- [ ] **Step 3: Add imports + state machine to `epic.py`**

At the top of `epic.py`, add `from dataclasses import dataclass, replace` to the imports block.

After `WMO_CODES` constant, add:

```python
MODE_PHOTO = 'photo'
MODE_BLENDING = 'blending'
MODE_OVERLAY = 'overlay'


@dataclass
class AppState:
    mode: str
    current_idx: int
    num_photos: int
    next_photo_swap_at: datetime.datetime
    next_image_api_check_at: datetime.datetime
    overlay_dismiss_at: 'datetime.datetime | None'
    blend_started_at: 'datetime.datetime | None'
    last_image_data: str


def _advance_photo(state, now, rotate_delay):
    next_idx = (state.current_idx + 1) % max(state.num_photos, 1)
    return replace(
        state,
        mode=MODE_PHOTO,
        current_idx=next_idx,
        blend_started_at=None,
        next_photo_swap_at=now + datetime.timedelta(seconds=rotate_delay),
    )


def tick_state(state, events, now, blend_enabled, rotate_delay, blend_duration):
    tap = any(getattr(e, 'type', None) == pygame.MOUSEBUTTONDOWN for e in events)

    # Tap handling — highest priority
    if tap:
        if state.mode == MODE_OVERLAY:
            return replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
        if state.mode == MODE_BLENDING:
            advanced = _advance_photo(state, now, rotate_delay)
            return replace(
                advanced,
                mode=MODE_OVERLAY,
                overlay_dismiss_at=now + datetime.timedelta(seconds=OVERLAY_AUTO_DISMISS_SEC),
            )
        return replace(
            state,
            mode=MODE_OVERLAY,
            overlay_dismiss_at=now + datetime.timedelta(seconds=OVERLAY_AUTO_DISMISS_SEC),
        )

    # Overlay auto-dismiss
    if state.mode == MODE_OVERLAY:
        if state.overlay_dismiss_at is not None and now >= state.overlay_dismiss_at:
            return replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
        return state

    # Blend completion
    if state.mode == MODE_BLENDING:
        if state.blend_started_at is None:
            return state
        if (now - state.blend_started_at) >= datetime.timedelta(seconds=blend_duration):
            return _advance_photo(state, now, rotate_delay)
        return state

    # Photo swap due
    if state.mode == MODE_PHOTO and now >= state.next_photo_swap_at and state.num_photos > 0:
        if blend_enabled and state.num_photos > 1:
            return replace(state, mode=MODE_BLENDING, blend_started_at=now)
        return _advance_photo(state, now, rotate_delay)

    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestAppState -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `pytest -v`
Expected: All previous tests still PASS. New tests PASS. Existing `rotate_photos` /
`blend_between_photos` tests still PASS — those functions are not removed yet.

- [ ] **Step 6: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add AppState + tick_state pure state machine"
```

---

## Task 6: Single-frame render helpers (`render_photo`, `render_blend`)

Pure refactor — adds new functions that render exactly one frame each. The existing
`rotate_photos` and `blend_between_photos` stay in place for now (removed in Task 8).

**Files:**
- Modify: `epic.py` (two new functions)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# render_photo / render_blend single-frame helpers
# ============================================================


class TestRenderHelpers:
    def _make_screen(self):
        return pygame.Surface((480, 480))

    def _make_image(self, color):
        s = pygame.Surface((480, 480))
        s.fill(color)
        return s

    def test_render_photo_blits_image(self):
        screen = self._make_screen()
        photo = self._make_image((10, 20, 30))
        # Should not raise; should leave screen with the photo's color at (0,0)
        epic.render_photo(screen, photo)
        assert screen.get_at((0, 0))[:3] == (10, 20, 30)

    def test_render_blend_alpha_zero_shows_old(self):
        screen = self._make_screen()
        old = self._make_image((255, 0, 0))
        new = self._make_image((0, 255, 0))
        epic.render_blend(screen, old, new, alpha=0)
        assert screen.get_at((0, 0))[:3] == (255, 0, 0)

    def test_render_blend_alpha_full_shows_new(self):
        screen = self._make_screen()
        old = self._make_image((255, 0, 0))
        new = self._make_image((0, 255, 0))
        epic.render_blend(screen, old, new, alpha=255)
        # With full alpha, new image dominates.
        assert screen.get_at((0, 0))[:3] == (0, 255, 0)

    def test_render_blend_clamps_alpha(self):
        screen = self._make_screen()
        old = self._make_image((255, 0, 0))
        new = self._make_image((0, 255, 0))
        # alpha values outside [0, 255] are clamped, not crashed
        epic.render_blend(screen, old, new, alpha=500)
        assert screen.get_at((0, 0))[:3] == (0, 255, 0)
        epic.render_blend(screen, old, new, alpha=-100)
        assert screen.get_at((0, 0))[:3] == (255, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestRenderHelpers -v`
Expected: FAIL — `render_photo`, `render_blend` not defined.

- [ ] **Step 3: Add render helpers to `epic.py`**

After `tick_state`, add:

```python
def render_photo(screen, image):
    screen.blit(image, (0, 0))


def render_blend(screen, old_image, new_image, alpha):
    clamped = max(0, min(255, int(alpha)))
    screen.blit(old_image, (0, 0))
    new_image.set_alpha(clamped)
    screen.blit(new_image, (0, 0))


def compute_blend_alpha(now, started_at, duration_seconds):
    if started_at is None or duration_seconds <= 0:
        return 255
    elapsed = (now - started_at).total_seconds()
    fraction = elapsed / duration_seconds
    return max(0, min(255, int(fraction * 255)))
```

Then add a test for `compute_blend_alpha` to the same class:

```python
    def test_compute_blend_alpha_start(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        assert epic.compute_blend_alpha(t0, t0, 5) == 0

    def test_compute_blend_alpha_mid(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        mid = t0 + datetime.timedelta(seconds=2.5)
        # 2.5/5 = 0.5 → 127 or 128 acceptable
        result = epic.compute_blend_alpha(mid, t0, 5)
        assert 125 <= result <= 130

    def test_compute_blend_alpha_end(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        end = t0 + datetime.timedelta(seconds=5)
        assert epic.compute_blend_alpha(end, t0, 5) == 255

    def test_compute_blend_alpha_past_end(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        past = t0 + datetime.timedelta(seconds=10)
        assert epic.compute_blend_alpha(past, t0, 5) == 255

    def test_compute_blend_alpha_none_started(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        assert epic.compute_blend_alpha(t0, None, 5) == 255
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestRenderHelpers -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add single-frame render helpers + blend alpha calc"
```

---

## Task 7: `render_overlay`

**Files:**
- Modify: `epic.py` (new function)
- Test: `test_epic.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `test_epic.py`:

```python
# ============================================================
# render_overlay
# ============================================================


class TestRenderOverlay:
    def _screen(self):
        return pygame.Surface((480, 480))

    def _full_cache(self):
        return {
            'temp_c': -8,
            'weather_code': 2,
            'condition': 'Partly Cloudy',
            'sunrise': '06:42',
            'sunset': '19:08',
            'rain_today': (60, 2.0),
            'rain_tomorrow': (80, 5.4),
            'fetched_at': datetime.datetime(2026, 5, 2, 11, 50),
        }

    def test_render_with_full_cache(self):
        screen = self._screen()
        screen.fill((255, 255, 255))
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        # Should not raise.
        epic.render_overlay(screen, self._full_cache(), now)
        # Overlay dims the surface — center pixel should no longer be pure white.
        cx, cy = 240, 240
        r, g, b = screen.get_at((cx, cy))[:3]
        assert (r, g, b) != (255, 255, 255)

    def test_render_with_no_cache(self):
        screen = self._screen()
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        # Should not raise even with None cache.
        epic.render_overlay(screen, None, now)

    def test_render_with_empty_cache(self):
        screen = self._screen()
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        epic.render_overlay(screen, {}, now)

    def test_render_with_partial_cache(self):
        screen = self._screen()
        cache = {
            'temp_c': None,
            'weather_code': None,
            'condition': '—',
            'sunrise': None,
            'sunset': None,
            'rain_today': (None, None),
            'rain_tomorrow': (None, None),
            'fetched_at': datetime.datetime(2026, 5, 2, 11, 0),
        }
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        epic.render_overlay(screen, cache, now)

    def test_render_marks_stale(self):
        screen = self._screen()
        cache = self._full_cache()
        cache['fetched_at'] = datetime.datetime(2026, 5, 2, 10, 0)  # 2h ago
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        # Just smoke — should not raise.
        epic.render_overlay(screen, cache, now)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_epic.py::TestRenderOverlay -v`
Expected: FAIL — `render_overlay` not defined.

- [ ] **Step 3: Implement `render_overlay` in `epic.py`**

After the render helpers, add:

```python
def _format_temp(temp_c):
    if temp_c is None:
        return '—'
    return str(temp_c) + '°C'


def _format_rain(label, prob_mm):
    prob, mm = prob_mm
    prob_text = '—' if prob is None else str(int(prob)) + '%'
    mm_text = '—' if mm is None else "{:.1f}mm".format(mm)
    return label + '  ' + prob_text + ' · ' + mm_text


def _format_sun(sunrise, sunset):
    sr = '—' if sunrise is None else sunrise
    ss = '—' if sunset is None else sunset
    return '↑ ' + sr + '   ↓ ' + ss


def render_overlay(screen, cache, now):
    # Dim layer over photo
    dim = pygame.Surface(DISPLAY_SIZE)
    dim.fill((0, 0, 0))
    dim.set_alpha(180)
    screen.blit(dim, (0, 0))

    white = (245, 245, 245)
    yellow = (220, 200, 80)

    clock_font = pygame.font.SysFont('dejavusans', 72, bold=True)
    temp_font = pygame.font.SysFont('dejavusans', 96, bold=True)
    cond_font = pygame.font.SysFont('dejavusans', 28)
    small_font = pygame.font.SysFont('dejavusans', 24)
    stale_font = pygame.font.SysFont('dejavusans', 16)

    cx = DISPLAY_SIZE[0] // 2

    def draw_centered(surface, font, text, color, y):
        rendered = font.render(text, True, color)
        rect = rendered.get_rect(center=(cx, y))
        surface.blit(rendered, rect)

    # Clock
    draw_centered(screen, clock_font, now.strftime('%H:%M'), white, 100)

    # Stale indicator (under clock)
    if cache and is_weather_stale(cache, WEATHER_REFRESH_MIN, now):
        fetched = cache.get('fetched_at')
        if fetched is not None:
            label = '⚠ stale ' + fetched.strftime('%H:%M')
            draw_centered(screen, stale_font, label, yellow, 140)

    # Weather block (or placeholders if no cache)
    if not cache:
        draw_centered(screen, temp_font, '—', white, 215)
        draw_centered(screen, cond_font, 'loading…', white, 295)
        return

    draw_centered(screen, temp_font, _format_temp(cache.get('temp_c')), white, 215)
    draw_centered(screen, cond_font, cache.get('condition', '—'), white, 295)
    draw_centered(screen, small_font, _format_sun(cache.get('sunrise'), cache.get('sunset')), white, 345)
    draw_centered(screen, small_font, _format_rain('Today', cache.get('rain_today', (None, None))), white, 385)
    draw_centered(screen, small_font, _format_rain('Tomorrow', cache.get('rain_tomorrow', (None, None))), white, 420)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_epic.py::TestRenderOverlay -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add epic.py test_epic.py
git commit -m "feat: add render_overlay with dim layer + clock + weather stack"
```

---

## Task 8: Refactor `main()` to frame-tick event loop + remove obsolete loop functions

This is the biggest single edit. Replace `main`, `rotate_photos`, and
`blend_between_photos` with a frame-tick implementation that uses `tick_state`,
`render_photo`, `render_blend`, and `render_overlay`.

**Files:**
- Modify: `epic.py` (replace `main`, `rotate_photos`, `blend_between_photos`)
- Modify: `test_epic.py` (delete tests for removed functions, add minimal `main` smoke
  test)

- [ ] **Step 1: Write a `main` smoke test that drives one tick + quits**

Append to `test_epic.py`:

```python
# ============================================================
# main loop — minimal smoke
# ============================================================


class TestMainLoopSmoke:
    def test_main_exits_cleanly_on_quit_event(self, monkeypatch):
        # Pre-populate one fake photo on disk so the loop has something to render.
        import tempfile

        tmpdir = tempfile.mkdtemp()
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            surf = pygame.Surface((480, 480))
            pygame.image.save(surf, '0.jpg')
            pygame.image.save(surf, 'loading.jpg')

            monkeypatch.setattr(epic, 'geocode_city', lambda _: (52.23, 21.01, 'Warsaw'))
            monkeypatch.setattr(
                epic,
                'fetch_weather',
                lambda lat, lon: {
                    'temp_c': 0,
                    'weather_code': 0,
                    'condition': 'Clear',
                    'sunrise': '06:00',
                    'sunset': '18:00',
                    'rain_today': (0, 0.0),
                    'rain_tomorrow': (0, 0.0),
                    'fetched_at': datetime.datetime.now(),
                },
            )
            monkeypatch.setattr(
                epic,
                'get_epic_images_json',
                lambda: [{'date': '2026-05-02 12:00:00', 'image': 'x'}],
            )
            monkeypatch.setattr(epic, 'save_photos', lambda urls, screen=None: None)
            monkeypatch.setattr(epic, 'init_display', lambda: pygame.Surface((480, 480)))

            ticks = {'n': 0}

            def fake_event_get():
                ticks['n'] += 1
                if ticks['n'] >= 3:
                    return [pygame.event.Event(pygame.QUIT)]
                return []

            monkeypatch.setattr(pygame.event, 'get', fake_event_get)

            # Should return cleanly without raising.
            epic.main()
        finally:
            os.chdir(old_cwd)
```

- [ ] **Step 2: Run the smoke test to confirm it currently fails or passes (baseline)**

Run: `pytest test_epic.py::TestMainLoopSmoke -v`
Expected: Likely TIMEOUT or FAIL — current `main()` blocks on `time.sleep`.

- [ ] **Step 3: Replace `main()`, delete `rotate_photos` and `blend_between_photos`**

In `epic.py`:

(a) Delete the entire `rotate_photos` function (the 20-line definition starting at
`def rotate_photos(num_photos, rotate_delay, blend_enabled=False, blend_time=5, screen=None):`).

(b) Delete the entire `blend_between_photos` function.

(c) Replace the entire `main()` function with:

```python
def _maybe_kick_tap_refresh(lat, lon, cache_ref, lock):
    # Spawn a one-shot fetch thread if cache is older than WEATHER_TAP_REFRESH_MIN.
    cache = cache_ref.get('value')
    if not is_weather_stale(cache, WEATHER_TAP_REFRESH_MIN, datetime.datetime.now()):
        return
    if cache_ref.get('inflight'):
        return
    cache_ref['inflight'] = True

    def _worker():
        try:
            new_cache = fetch_weather(lat, lon)
            with lock:
                cache_ref['value'] = new_cache
        except Exception as exc:  # network failure — keep prior cache
            print('tap-driven weather fetch failed:', exc)
        finally:
            cache_ref['inflight'] = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _weather_refresh_loop(lat, lon, cache_ref, lock):
    while True:
        try:
            new_cache = fetch_weather(lat, lon)
            with lock:
                cache_ref['value'] = new_cache
        except Exception as exc:
            print('background weather fetch failed:', exc)
        time.sleep(WEATHER_REFRESH_MIN * 60)


def _maybe_check_for_new_images(state, screen, now):
    # Returns updated state if a new photo set was downloaded.
    if now < state.next_image_api_check_at:
        return state
    print(str(now) + ' Checking for new images.')
    try:
        image_data = get_epic_images_json()
    except Exception as exc:
        print('image API check failed:', exc)
        return replace(state, next_image_api_check_at=now + datetime.timedelta(minutes=check_delay))
    newest = image_data[0]['date'] if image_data else ''
    if newest and newest != state.last_image_data:
        print('Ooh! New Images! OLD=' + state.last_image_data + ' NEW=' + newest)
        urls = create_image_urls(image_data)
        save_photos(urls, screen)
        return replace(
            state,
            num_photos=len(urls),
            current_idx=0,
            last_image_data=newest,
            next_image_api_check_at=now + datetime.timedelta(minutes=check_delay),
            next_photo_swap_at=now + datetime.timedelta(seconds=rotate_delay),
        )
    return replace(state, next_image_api_check_at=now + datetime.timedelta(minutes=check_delay))


def main():
    # Geocode city BEFORE display init so config errors fail fast without flashing display.
    lat, lon, display_name = geocode_city(CITY_NAME)
    print('Weather for: ' + display_name + ' (' + str(lat) + ', ' + str(lon) + ')')

    screen = init_display()

    # Show loading splash
    try:
        loading = pygame.image.load(r'./loading.jpg')
        screen.blit(loading, (0, 0))
        pygame.display.flip()
    except Exception as exc:
        print('loading splash skipped:', exc)

    print('Checking for new photos every ' + str(check_delay) + ' minutes')
    print('Rotating photos every ' + str(rotate_delay) + ' seconds')

    # Weather cache (mutable holder so threads can replace it)
    weather_cache_ref = {'value': None, 'inflight': False}
    weather_lock = threading.Lock()

    # Kick off background refresh thread
    refresher = threading.Thread(
        target=_weather_refresh_loop,
        args=(lat, lon, weather_cache_ref, weather_lock),
        daemon=True,
    )
    refresher.start()

    now = datetime.datetime.now()
    state = AppState(
        mode=MODE_PHOTO,
        current_idx=0,
        num_photos=0,
        next_photo_swap_at=now,
        next_image_api_check_at=now,  # check immediately on first tick
        overlay_dismiss_at=None,
        blend_started_at=None,
        last_image_data='',
    )

    clock = pygame.time.Clock()
    running = True

    while running:
        now = datetime.datetime.now()
        events = pygame.event.get()

        # QUIT handling — never call pygame.quit() inside the loop body
        for event in events:
            if event.type == pygame.QUIT:
                running = False
                break
        if not running:
            break

        # If user tapped, also kick a one-shot refresh if cache is stale enough
        if any(e.type == pygame.MOUSEBUTTONDOWN for e in events):
            _maybe_kick_tap_refresh(lat, lon, weather_cache_ref, weather_lock)

        # Image API check (downloads when new data appears)
        state = _maybe_check_for_new_images(state, screen, now)

        # Advance state machine
        state = tick_state(
            state, events, now,
            blend_enabled=enable_blending,
            rotate_delay=rotate_delay,
            blend_duration=blending_duration,
        )

        # Render
        if state.num_photos > 0:
            current_img = pygame.image.load(r'./' + str(state.current_idx) + '.jpg')
            if state.mode == MODE_BLENDING:
                prev_idx = (state.current_idx - 1) % state.num_photos
                old_img = pygame.image.load(r'./' + str(prev_idx) + '.jpg')
                alpha = compute_blend_alpha(now, state.blend_started_at, blending_duration)
                render_blend(screen, old_img, current_img, alpha)
            else:
                render_photo(screen, current_img)

            if state.mode == MODE_OVERLAY:
                with weather_lock:
                    cache_snapshot = weather_cache_ref.get('value')
                render_overlay(screen, cache_snapshot, now)

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()
```

(d) Add `import threading` to the imports block at the top of `epic.py`.

- [ ] **Step 4: Remove obsolete tests**

In `test_epic.py`, delete the test classes that test the now-removed functions:
- `TestRotatePhotos`
- `TestBlendBetweenPhotos`

Use Grep first to confirm class names if uncertain:
```bash
grep -n "^class Test" test_epic.py
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: All previous still-relevant tests PASS, plus all new tests from Tasks 1-7
PASS, plus the new smoke test PASSES. Coverage may dip — measured in Task 10.

- [ ] **Step 6: Commit**

```bash
git add epic.py test_epic.py
git commit -m "refactor: replace blocking sleep loops with frame-tick state machine"
```

---

## Task 9: Background + tap-driven refresh — verify wiring

The refresh helpers were added in Task 8. This task adds dedicated unit tests for the
threading wiring (without spinning real threads) so behavior is documented.

**Files:**
- Modify: `test_epic.py` (new test class)

- [ ] **Step 1: Write tests for `_maybe_kick_tap_refresh`**

Append to `test_epic.py`:

```python
# ============================================================
# Tap-driven refresh wiring
# ============================================================


class TestTapRefresh:
    def test_skips_when_fresh(self, monkeypatch):
        cache_ref = {
            'value': {'fetched_at': datetime.datetime.now()},  # fresh
            'inflight': False,
        }
        lock = mock.MagicMock()
        called = []
        monkeypatch.setattr(epic, 'fetch_weather', lambda lat, lon: called.append((lat, lon)))
        # Use a fake Thread so .start() does not spin a real worker
        fake_thread = mock.MagicMock()
        monkeypatch.setattr(epic.threading, 'Thread', lambda *a, **kw: fake_thread)

        epic._maybe_kick_tap_refresh(0.0, 0.0, cache_ref, lock)

        assert fake_thread.start.called is False
        assert called == []

    def test_kicks_when_stale(self, monkeypatch):
        old = datetime.datetime.now() - datetime.timedelta(minutes=epic.WEATHER_TAP_REFRESH_MIN + 1)
        cache_ref = {
            'value': {'fetched_at': old},
            'inflight': False,
        }
        lock = mock.MagicMock()
        # Capture the Thread target so we can invoke it inline
        captured = {}

        def fake_thread_factory(target=None, daemon=None, **kwargs):
            captured['target'] = target
            t = mock.MagicMock()
            t.start = lambda: target() if target else None
            return t

        monkeypatch.setattr(epic.threading, 'Thread', fake_thread_factory)
        monkeypatch.setattr(
            epic,
            'fetch_weather',
            lambda lat, lon: {
                'temp_c': 1, 'weather_code': 0, 'condition': 'Clear',
                'sunrise': '06:00', 'sunset': '18:00',
                'rain_today': (0, 0.0), 'rain_tomorrow': (0, 0.0),
                'fetched_at': datetime.datetime.now(),
            },
        )

        epic._maybe_kick_tap_refresh(52.23, 21.01, cache_ref, lock)

        assert captured.get('target') is not None
        assert cache_ref['value']['temp_c'] == 1  # worker ran inline via fake start
        assert cache_ref['inflight'] is False  # cleared in `finally`

    def test_skips_when_already_inflight(self, monkeypatch):
        cache_ref = {'value': None, 'inflight': True}
        lock = mock.MagicMock()
        called = []
        monkeypatch.setattr(epic, 'fetch_weather', lambda lat, lon: called.append(1))
        epic._maybe_kick_tap_refresh(0.0, 0.0, cache_ref, lock)
        assert called == []

    def test_swallows_fetch_error(self, monkeypatch):
        old = datetime.datetime.now() - datetime.timedelta(minutes=60)
        cache_ref = {'value': {'fetched_at': old}, 'inflight': False}
        lock = mock.MagicMock()

        def fake_thread_factory(target=None, daemon=None, **kwargs):
            t = mock.MagicMock()
            t.start = lambda: target()
            return t

        monkeypatch.setattr(epic.threading, 'Thread', fake_thread_factory)

        def boom(lat, lon):
            raise RuntimeError('network down')

        monkeypatch.setattr(epic, 'fetch_weather', boom)

        # Should NOT raise
        epic._maybe_kick_tap_refresh(0.0, 0.0, cache_ref, lock)
        # Cache was preserved (only fetched_at present)
        assert cache_ref['value']['fetched_at'] == old
        assert cache_ref['inflight'] is False
```

- [ ] **Step 2: Run tests**

Run: `pytest test_epic.py::TestTapRefresh -v`
Expected: All 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add test_epic.py
git commit -m "test: cover tap-driven weather refresh wiring"
```

---

## Task 10: Format, sanity check, full suite, manual review

**Files:**
- Modify: `epic.py` and `test_epic.py` (formatting only)

- [ ] **Step 1: Run isort + black**

On Windows:
```
format_source.cmd
```

On Linux/macOS (or if the cmd file is not available):
```bash
isort . --sp=.isort.cfg
black . --config=pyproject.toml
```

Expected: Reformatting applied silently, exit code 0.

- [ ] **Step 2: Sanity check imports + syntax**

Run:
```bash
python -c "import ast; ast.parse(open('epic.py').read()); print('OK')"
```
Expected: prints `OK`.

- [ ] **Step 3: Run full test suite with coverage**

Run:
```bash
pytest -v --cov=epic --cov-report=term-missing
```

Expected:
- All tests PASS.
- Coverage on `epic.py` ≥ 95%.
- If a few branches are uncovered (e.g., the daemon `_weather_refresh_loop` infinite
  loop), confirm they are intentionally untested and skip — do not write tests that
  spin real threads.

If coverage drops below 95%, identify the missing branches by reading the
`term-missing` output and add a focused test for each.

- [ ] **Step 4: Manual sanity (where possible without Pi hardware)**

Open `epic.py` in your editor and re-read the new functions in this order:

1. `geocode_city`
2. `fetch_weather`
3. `is_weather_stale`
4. `tick_state`
5. `render_overlay`
6. `main`

Confirm:
- All HTTP calls have `timeout=HTTP_TIMEOUT`.
- No `time.sleep` calls remain inside the rendering / state-machine path (only in the
  `_weather_refresh_loop` daemon thread, which is correct).
- No `pygame.quit()` calls inside the event loop body (only at the bottom of `main`,
  after the loop exits).
- No bare `except:` — all are `except Exception` with a printed message.

- [ ] **Step 5: Commit format-only changes (if any)**

```bash
git add epic.py test_epic.py
git diff --cached --stat
git commit -m "style: apply isort + black"
```
(Skip this commit if `git diff --cached --stat` shows no changes.)

- [ ] **Step 6: Final summary commit (optional, if anything else moved)**

If the working tree is clean, this task is done. Otherwise commit any leftover
changes with a focused message.

---

## Self-Review Notes

Verified against `docs/superpowers/specs/2026-05-02-interactive-weather-overlay-design.md`:

| Spec section                              | Implementing task(s) |
|-------------------------------------------|----------------------|
| Tap toggle + 60 s safety auto-dismiss     | 5, 8                 |
| Layout (clock, temp, condition, sun, rain) | 7                   |
| Stale indicator under clock               | 7                    |
| Open-Meteo geocode + forecast             | 2, 3                 |
| Cache + staleness                         | 4                    |
| State machine `PHOTO`/`BLENDING`/`OVERLAY` | 5                   |
| Frame-tick event loop                     | 8                    |
| Background daemon refresh thread          | 8                    |
| Tap-driven one-shot refresh               | 8, 9                 |
| Geocode before `init_display`             | 8                    |
| Failure handling (timeouts, error swallow) | 3, 8                |
| Test coverage ≥ 95 %                      | 10                   |
| Single-file architecture preserved        | All tasks            |
| No new third-party dependencies           | All tasks            |

Naming consistency check: `MODE_PHOTO` / `MODE_BLENDING` / `MODE_OVERLAY` are used in
both Tasks 5 and 8. `AppState` field names are used identically in `tick_state` and
`main`. `is_weather_stale(cache, refresh_min, now)` signature is used identically in
Tasks 4, 7, and 8. `fetch_weather(lat, lon)` signature is used identically in Tasks 3,
8, and 9.
