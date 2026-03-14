# RaspberryPi-epic — Deep Code Analysis

## Date: 2026-03-14

## 1. Project Overview

- **Purpose**: Display NASA EPIC Earth images on a Raspberry Pi with attached display
- **Stack**: Python 3.11, Pygame 2.6.1, Requests 2.32.5
- **Size**: 186 lines of code (epic.py) + 276 lines of tests (test_epic.py)
- **Test Coverage**: 99% (45 tests)
- **License**: Not specified

## 2. Architecture

### Structure
```
epic.py              — Main application (single file)
test_epic.py         — Test suite
loading.jpg          — Splash screen image
requirements.txt     — Dependencies
pyproject.toml       — Project config (black, isort, pytest)
```

### Data Flow
```
NASA EPIC API → JSON → Image URLs → Download & Crop → Save as numbered JPGs → Rotate display
```

### Key Functions
| Function | Lines | Purpose |
|----------|-------|---------|
| `get_epic_images_json()` | 3 | Fetch latest EPIC images metadata |
| `create_image_urls()` | 14 | Build download URLs from metadata |
| `save_photos()` | 13 | Download, crop, resize, save images |
| `blend_between_photos()` | 20 | Smooth transition between images |
| `rotate_photos()` | 20 | Cycle through saved images |
| `init_display()` | 9 | Initialize Pygame display |
| `main()` | 48 | Main loop: check API, download, display |

## 3. Issues Found

### 🔴 Critical

1. **No error handling for API calls** (line 18-20)
   - `get_epic_images_json()` does no try/except
   - Network errors, timeouts, or API downtime crash the entire app
   - **Fix**: Add retry logic with exponential backoff

2. **No error handling for image downloads** (line 32-42)
   - `urlopen(imageurl)` can fail on network issues
   - No timeout parameter — hangs indefinitely on slow connections
   - **Fix**: Add timeout and retry, wrap in try/except

3. **`pygame.quit()` called inside event loop** (line 137)
   - When QUIT event fires in main loop, `pygame.quit()` is called but `running` is set to False
   - But `rotate_photos()` also calls `pygame.quit()` independently
   - This can cause double-quit and unpredictable behavior
   - **Fix**: Only set running flag, quit once at the end

### 🟡 High Priority

4. **No graceful shutdown** (line 115-186)
   - No signal handler for SIGTERM/SIGINT
   - If the process is killed, temporary files remain
   - **Fix**: Add signal handler, cleanup function

5. **Hardcoded display size and crop parameters** (lines 12-14)
   - `DISPLAY_SIZE = (480, 480)` works for specific display only
   - No auto-detection of display resolution
   - **Fix**: Use `pygame.display.Info()` or config file

6. **No disk space check before downloading** (line 32-42)
   - Downloads all images (can be 20+ at ~200KB each)
   - Doesn't clean up old images before downloading new ones
   - **Fix**: Add disk space check, clean up old files

7. **Sleep-based timing in blend** (line 80)
   - `time.sleep(target_duration / 255)` is inaccurate
   - Doesn't account for processing time per frame
   - **Fix**: Use clock-based timing with `pygame.time.Clock`

### 🟢 Low Priority / Improvements

8. **String concatenation for URL building** (lines 26-34)
   - Uses manual string concatenation with `+`
   - **Fix**: Use f-strings or `urllib.parse.urljoin`

9. **No logging framework** (all print statements)
   - Uses `print()` instead of `logging`
   - No log levels, no file output
   - **Fix**: Add `logging` module with configurable levels

10. **Images saved as numbered files** (line 44)
    - `0.jpg`, `1.jpg`, etc. — no metadata about what each image is
    - **Fix**: Include date in filename, or save metadata JSON

11. **No configuration file**
    - All settings hardcoded (check_delay, rotate_delay, display size)
    - **Fix**: Add YAML/JSON config file or argparse

12. **`first_run` flag pattern** (line 153)
    - Uses `first_run = True` then `first_run = False` pattern
    - **Fix**: Use `last_check` initialized to epoch instead

## 4. Security Considerations

- **HTTP URLs**: Image downloads use HTTPS (good)
- **No input validation**: API response is trusted without validation
- **No file path sanitization**: Image filenames from API used directly (low risk — only date components)

## 5. Performance Notes

- **Image processing per frame**: Each rotation reload from disk (`pygame.image.load`) — could cache in memory
- **Blending**: 255 iterations with display flip per frame — could reduce to 60fps equivalent
- **API polling**: Every 2 hours is reasonable for EPIC (images update ~hourly)

## 6. Recommended Improvements (Priority Order)

1. **Add error handling** — try/except around network calls with retry
2. **Add signal handlers** — graceful shutdown on SIGTERM
3. **Use logging module** — replace all print() calls
4. **Add config file support** — YAML or argparse for settings
5. **Cache images in memory** — avoid re-reading from disk each rotation
6. **Use pygame.time.Clock** — frame-rate based timing instead of sleep
7. **Auto-detect display size** — query display info at startup
8. **Add systemd service file** — for auto-start on boot
9. **Add health check endpoint** — simple HTTP server for monitoring
10. **Cleanup old images** — remove old files before downloading new batch

## 7. Dependency Analysis

| Package | Version | Latest | Status |
|---------|---------|--------|--------|
| pygame | 2.6.1 | 2.6.1 | ✅ Current |
| requests | 2.32.5 | 2.32.5 | ✅ Current |

No known vulnerabilities in current dependency versions.

## 8. Test Quality Assessment

- **Coverage**: 99% (excellent)
- **Test types**: Unit tests with mocking, integration tests for main()
- **Missing**: No performance tests, no integration test with real API
- **Quality**: Tests properly mock external dependencies (pygame, requests, urlopen)
- **Recommendation**: Add a smoke test that actually calls the EPIC API (skippable in CI)
