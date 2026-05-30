# Scheduled Night Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the display on during the day and off at night on a fixed daily schedule (default on 08:00–22:00).

**Architecture:** A pure predicate `is_screen_on(now, on, off)` decides day vs night; a best-effort `_set_backlight(on)` shells out to `pinctrl`; the main loop edge-triggers a black frame + backlight off going dark, ignores input while off, and idles at 4 fps. `tick_state` is untouched.

**Tech Stack:** Python 3.9+, pygame, `subprocess` (stdlib), pytest + unittest.mock.

---

### Task 1: Config constants

**Files:**
- Modify: `epic.py` (settings block, ~line 33) and top import block.

- [ ] **Step 1: Add `import subprocess`** to the stdlib import block at the top of `epic.py` (alphabetical, after `struct`/`sys`).

- [ ] **Step 2: Add constants** after `OVERLAY_AUTO_DISMISS_SEC = 60`:

```python
# Night-mode (scheduled screen on/off) settings
SCREEN_ON = os.environ.get('EPIC_SCREEN_ON', '08:00')
SCREEN_OFF = os.environ.get('EPIC_SCREEN_OFF', '22:00')
NIGHT_MODE = not os.environ.get('EPIC_NIGHT_DISABLE')
BACKLIGHT_GPIO = int(os.environ.get('EPIC_BACKLIGHT_GPIO', '19'))
```

- [ ] **Step 3: Add tests** in `test_epic.py` (new class):

```python
class TestNightModeDefaults:
    def test_screen_on_default(self):
        assert epic.SCREEN_ON == '08:00'

    def test_screen_off_default(self):
        assert epic.SCREEN_OFF == '22:00'

    def test_night_mode_enabled_default(self):
        assert epic.NIGHT_MODE is True

    def test_backlight_gpio_default(self):
        assert epic.BACKLIGHT_GPIO == 19
```

- [ ] **Step 4: Run** `pytest test_epic.py::TestNightModeDefaults -q` → PASS.

- [ ] **Step 5: Commit** `feat(night): add night-mode config constants`.

---

### Task 2: `_parse_clock`

**Files:**
- Modify: `epic.py` (helpers section, after `_parse_hhmm`).
- Test: `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestParseClock:
    def test_basic(self):
        assert epic._parse_clock('08:00') == datetime.time(8, 0)

    def test_minutes(self):
        assert epic._parse_clock('22:30') == datetime.time(22, 30)

    def test_whitespace(self):
        assert epic._parse_clock('  07:15 ') == datetime.time(7, 15)

    @pytest.mark.parametrize('bad', ['25:00', '08:99', 'abc', '', '8', '08:00:00'])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            epic._parse_clock(bad)
```

- [ ] **Step 2: Run** `pytest test_epic.py::TestParseClock -q` → FAIL (`_parse_clock` not defined).

- [ ] **Step 3: Implement** after `_parse_hhmm`:

```python
def _parse_clock(hhmm):
    parts = hhmm.strip().split(':')
    if len(parts) != 2:
        raise ValueError('bad time string: ' + repr(hhmm))
    return datetime.time(hour=int(parts[0]), minute=int(parts[1]))
```

- [ ] **Step 4: Run** `pytest test_epic.py::TestParseClock -q` → PASS.

- [ ] **Step 5: Commit** `feat(night): add _parse_clock helper`.

---

### Task 3: `is_screen_on`

**Files:**
- Modify: `epic.py` (after `is_weather_stale`).
- Test: `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestIsScreenOn:
    ON = datetime.time(8, 0)
    OFF = datetime.time(22, 0)

    def _at(self, h, m=0):
        return datetime.datetime(2026, 5, 30, h, m)

    def test_daytime_on(self):
        assert epic.is_screen_on(self._at(12), self.ON, self.OFF) is True

    def test_morning_before_on_off(self):
        assert epic.is_screen_on(self._at(7, 59), self.ON, self.OFF) is False

    def test_on_boundary_inclusive(self):
        assert epic.is_screen_on(self._at(8, 0), self.ON, self.OFF) is True

    def test_off_boundary_exclusive(self):
        assert epic.is_screen_on(self._at(22, 0), self.ON, self.OFF) is False

    def test_late_night_off(self):
        assert epic.is_screen_on(self._at(23, 30), self.ON, self.OFF) is False

    def test_minute_precision(self):
        assert epic.is_screen_on(self._at(21, 59), self.ON, self.OFF) is True

    def test_wrap_overnight_on(self):
        on, off = datetime.time(22, 0), datetime.time(8, 0)
        assert epic.is_screen_on(self._at(2), on, off) is True
        assert epic.is_screen_on(self._at(23), on, off) is True

    def test_wrap_overnight_off(self):
        on, off = datetime.time(22, 0), datetime.time(8, 0)
        assert epic.is_screen_on(self._at(12), on, off) is False
        assert epic.is_screen_on(self._at(8, 0), on, off) is False

    def test_degenerate_always_on(self):
        same = datetime.time(8, 0)
        assert epic.is_screen_on(self._at(3), same, same) is True
```

- [ ] **Step 2: Run** `pytest test_epic.py::TestIsScreenOn -q` → FAIL.

- [ ] **Step 3: Implement** after `is_weather_stale`:

```python
def is_screen_on(now, on_time, off_time):
    t = now.time()
    if on_time == off_time:
        return True
    if on_time < off_time:
        return on_time <= t < off_time
    return t >= on_time or t < off_time
```

- [ ] **Step 4: Run** `pytest test_epic.py::TestIsScreenOn -q` → PASS.

- [ ] **Step 5: Commit** `feat(night): add is_screen_on predicate`.

---

### Task 4: `_set_backlight`

**Files:**
- Modify: `epic.py` (near `_present` / display helpers).
- Test: `test_epic.py`.

- [ ] **Step 1: Write failing tests**:

```python
class TestSetBacklight:
    def test_on_drives_high(self):
        with mock.patch.dict(os.environ, {}, clear=False) as _, \
             mock.patch.object(epic.os.environ, 'get', wraps=os.environ.get):
            pass  # placeholder removed below

    # (real tests below)
```

Replace the placeholder above with:

```python
class TestSetBacklight:
    def _clean_env(self):
        return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                            if k != 'EPIC_NO_BACKLIGHT_CTL'}, clear=True)

    def test_on_drives_high(self):
        with self._clean_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            assert epic._set_backlight(True) is True
            args = run.call_args[0][0]
            assert args == ['pinctrl', 'set', str(epic.BACKLIGHT_GPIO), 'op', 'dh']

    def test_off_drives_low(self):
        with self._clean_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            assert epic._set_backlight(False) is True
            assert run.call_args[0][0][-1] == 'dl'

    def test_disabled_env_skips(self):
        with mock.patch.dict(os.environ, {'EPIC_NO_BACKLIGHT_CTL': '1'}), \
             mock.patch.object(epic.subprocess, 'run') as run:
            assert epic._set_backlight(True) is False
            run.assert_not_called()

    def test_pinctrl_missing_swallowed(self):
        with self._clean_env(), mock.patch.object(epic.subprocess, 'run',
                                                  side_effect=FileNotFoundError):
            assert epic._set_backlight(True) is False

    def test_nonzero_exit_returns_false(self):
        with self._clean_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=1)
            assert epic._set_backlight(True) is False

    def test_custom_gpio(self):
        with self._clean_env(), mock.patch.object(epic, 'BACKLIGHT_GPIO', 12), \
             mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            epic._set_backlight(True)
            assert run.call_args[0][0][2] == '12'
```

- [ ] **Step 2: Run** `pytest test_epic.py::TestSetBacklight -q` → FAIL.

- [ ] **Step 3: Implement** near the display helpers:

```python
def _set_backlight(on):
    if os.environ.get('EPIC_NO_BACKLIGHT_CTL'):
        return False
    level = 'dh' if on else 'dl'
    try:
        result = subprocess.run(
            ['pinctrl', 'set', str(BACKLIGHT_GPIO), 'op', level],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        print('backlight control unavailable:', exc)
        return False
    return result.returncode == 0
```

- [ ] **Step 4: Run** `pytest test_epic.py::TestSetBacklight -q` → PASS.

- [ ] **Step 5: Commit** `feat(night): add _set_backlight pinctrl helper`.

---

### Task 5: Wire night mode into `main()`

**Files:**
- Modify: `epic.py` `main()` — setup before the loop + a branch inside it.

- [ ] **Step 1: Add setup** after `screen = init_display()` and the splash block, before the `while running` loop (alongside the other pre-loop state):

```python
    on_t = _parse_clock(SCREEN_ON)
    off_t = _parse_clock(SCREEN_OFF)
    black_frame = pygame.Surface(DISPLAY_SIZE)
    black_frame.fill((0, 0, 0))
    screen_on = True
```

- [ ] **Step 2: Add the night branch** inside the loop, immediately after the `if not running: break` block and BEFORE the `_maybe_kick_tap_refresh` call:

```python
        now_on = (not NIGHT_MODE) or is_screen_on(now, on_t, off_t)
        if screen_on and not now_on:
            _set_backlight(False)
            state = replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
            screen.blit(black_frame, (0, 0))
            _present(screen)
        elif not screen_on and now_on:
            _set_backlight(True)
        screen_on = now_on

        if not now_on:
            clock.tick(4)
            continue
```

- [ ] **Step 3: Sanity-parse** `python -c "import ast; ast.parse(open('epic.py').read()); print('OK')"` → OK.

- [ ] **Step 4: Run full suite** `pytest -q` → all PASS.

- [ ] **Step 5: Commit** `feat(night): wire scheduled screen on/off into main loop`.

---

### Task 6: Coverage, format, docs

**Files:**
- Modify: `CLAUDE.md`.

- [ ] **Step 1: Coverage** `pytest --cov=epic --cov-report=term-missing -q` → ≥95%.

- [ ] **Step 2: Format** `isort . --sp=.isort.cfg ; black . --config=pyproject.toml` (or `format_source.cmd`).

- [ ] **Step 3: Update CLAUDE.md** — add `SCREEN_ON`/`SCREEN_OFF`/`NIGHT_MODE`/`BACKLIGHT_GPIO` to the settings list, a "Night mode" behaviour bullet, the env-var table, and the `pinctrl`-best-effort/black-frame-fallback note. List `is_screen_on`, `_parse_clock`, `_set_backlight` in the helpers section.

- [ ] **Step 4: Commit** `docs: document night-mode settings and behaviour`.
