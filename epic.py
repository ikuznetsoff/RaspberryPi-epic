import datetime
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from urllib.request import urlopen

import pygame
import requests

import epic_api

# Linux fb ioctl numbers (from <linux/fb.h>)
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

# Module-level framebuffer state. Populated by init_display() when EPIC_FBDEV is set.
_FB = None

# Settings
check_delay = 120  # minutes
rotate_delay = 20  # seconds
enable_blending = True  # True/False
blending_duration = 5  # second - how long to spend blending between 2 images

# Weather overlay settings
CITY_NAME = 'Warsaw'
WEATHER_REFRESH_MIN = 30
WEATHER_TAP_REFRESH_MIN = 10
HTTP_TIMEOUT = 10
OVERLAY_AUTO_DISMISS_SEC = 60

# Night-mode (scheduled screen on/off) settings
SCREEN_ON = os.environ.get('EPIC_SCREEN_ON', '08:00')
SCREEN_OFF = os.environ.get('EPIC_SCREEN_OFF', '22:00')
NIGHT_MODE = not os.environ.get('EPIC_NIGHT_DISABLE')
BACKLIGHT_GPIO = int(os.environ.get('EPIC_BACKLIGHT_GPIO', '19'))

# REST API / dashboard settings
API_HOST = os.environ.get('EPIC_API_HOST', '0.0.0.0')
API_PORT = int(os.environ.get('EPIC_API_PORT', '8080'))

DISPLAY_SIZE = (480, 480)
CROP_SIZE = 830
CROP_OFFSET = 125

# Edge-aware chroma reduction at save time; off by default (the panel's issue is a global cast, not edges).
EDGE_DESAT_STRENGTH = float(os.environ.get('EPIC_EDGE_DESAT', '0'))
EDGE_DESAT_THRESH = 20.0

# Per-channel display white-balance "r,g,b" gains applied at load; >1 boosts a channel, <1 cuts it.
RGB_GAIN = tuple(float(x) for x in os.environ.get('EPIC_RGB_GAIN', '1,1,1').split(','))
if len(RGB_GAIN) != 3:
    raise ValueError('EPIC_RGB_GAIN must be three comma-separated floats: "r,g,b"')

# Global saturation scale applied at load; <1 calms the panel's over-vivid rendering, 1 = no change.
SATURATION = float(os.environ.get('EPIC_SATURATION', '1'))

# Show a built-in grey/colour calibration pattern instead of Earth, to judge the panel by eye.
TEST_PATTERN = bool(os.environ.get('EPIC_TESTPATTERN'))

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


def _safe_index(seq, idx):
    if seq is None:
        return None
    if idx >= len(seq):
        return None
    return seq[idx]


def _parse_hhmm(iso_string):
    if iso_string is None:
        return None
    return iso_string.split('T', 1)[1][:5] if 'T' in iso_string else iso_string


def _parse_clock(hhmm):
    parts = hhmm.strip().split(':')
    if len(parts) != 2:
        raise ValueError('bad time string: ' + repr(hhmm))
    return datetime.time(hour=int(parts[0]), minute=int(parts[1]))


def fetch_weather(lat, lon):
    response = requests.get(
        'https://api.open-meteo.com/v1/forecast',
        params={
            'latitude': lat,
            'longitude': lon,
            'current': 'temperature_2m,weather_code,wind_speed_10m',
            'hourly': 'temperature_2m,precipitation_probability',
            'daily': 'sunrise,sunset,precipitation_probability_max,precipitation_sum',
            'forecast_days': 2,
            'timezone': 'auto',
            'temperature_unit': 'celsius',
            'wind_speed_unit': 'kmh',
        },
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    current = data.get('current', {})
    daily = data.get('daily', {})
    hourly = data.get('hourly', {})
    code = current.get('weather_code')
    temp = current.get('temperature_2m')
    wind = current.get('wind_speed_10m')
    sunrise_list = daily.get('sunrise', [])
    sunset_list = daily.get('sunset', [])
    prob_list = daily.get('precipitation_probability_max', [])
    sum_list = daily.get('precipitation_sum', [])
    return {
        'temp_c': int(round(temp)) if temp is not None else None,
        'weather_code': code,
        'condition': weather_code_to_text(code),
        'wind_kmh': int(round(wind)) if wind is not None else None,
        'sunrise': _parse_hhmm(_safe_index(sunrise_list, 0)),
        'sunset': _parse_hhmm(_safe_index(sunset_list, 0)),
        'rain_today': (_safe_index(prob_list, 0), _safe_index(sum_list, 0)),
        'rain_tomorrow': (_safe_index(prob_list, 1), _safe_index(sum_list, 1)),
        'hourly_time': hourly.get('time', []) or [],
        'hourly_temp': hourly.get('temperature_2m', []) or [],
        'hourly_prob': hourly.get('precipitation_probability', []) or [],
        'fetched_at': datetime.datetime.now(),
    }


def _select_next_24h(cache, now):
    if not cache:
        return []
    times = cache.get('hourly_time') or []
    temps = cache.get('hourly_temp') or []
    probs = cache.get('hourly_prob') or []
    if not times:
        return []
    floor_now = now.replace(minute=0, second=0, microsecond=0)
    start_idx = None
    for i, t_str in enumerate(times):
        try:
            dt = datetime.datetime.strptime(t_str, '%Y-%m-%dT%H:%M')
        except (ValueError, TypeError):
            continue
        if dt >= floor_now:
            start_idx = i
            break
    if start_idx is None:
        return []
    end_idx = min(start_idx + 24, len(times))
    return [(_safe_index(temps, j), _safe_index(probs, j)) for j in range(start_idx, end_idx)]


def _get_temp_range(cache, now):
    points = _select_next_24h(cache, now)
    valid = [t for t, _ in points if t is not None]
    if not valid:
        return None
    return int(round(min(valid))), int(round(max(valid)))


def is_weather_stale(cache, refresh_min, now):
    if not cache:
        return True
    fetched_at = cache.get('fetched_at')
    if fetched_at is None:
        return True
    return (now - fetched_at) > datetime.timedelta(minutes=refresh_min)


def is_screen_on(now, on_time, off_time):
    t = now.time()
    if on_time == off_time:
        return True
    if on_time < off_time:
        return on_time <= t < off_time
    return t >= on_time or t < off_time


def night_transition(prev_on, now_on):
    if prev_on and not now_on:
        return 'sleep'
    if not prev_on and now_on:
        return 'wake'
    return None


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

    if state.mode == MODE_OVERLAY:
        if state.overlay_dismiss_at is not None and now >= state.overlay_dismiss_at:
            return replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
        return state

    if state.mode == MODE_BLENDING:
        if state.blend_started_at is None:
            return state
        if (now - state.blend_started_at) >= datetime.timedelta(seconds=blend_duration):
            return _advance_photo(state, now, rotate_delay)
        return state

    if state.mode == MODE_PHOTO and now >= state.next_photo_swap_at and state.num_photos > 0:
        if blend_enabled and state.num_photos > 1:
            return replace(state, mode=MODE_BLENDING, blend_started_at=now)
        return _advance_photo(state, now, rotate_delay)

    return state


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


def _format_wind(wind_kmh):
    if wind_kmh is None:
        return 'Wind —'
    return 'Wind ' + str(wind_kmh) + ' km/h'


def render_forecast_chart(screen, cache, now, x, y, width, height):
    points = _select_next_24h(cache, now)
    if len(points) < 2:
        return False
    valid_temps = [t for t, _ in points if t is not None]
    if not valid_temps:
        return False

    t_min = min(valid_temps)
    t_max = max(valid_temps)
    if t_max - t_min < 1:
        t_max = t_min + 1

    n = len(points)
    step = width / n

    bar_layer = pygame.Surface((width, height), pygame.SRCALPHA)
    bar_color = (100, 150, 220, 150)
    bar_w = max(2, int(step * 0.7))
    for i, (_, prob) in enumerate(points):
        if prob is None:
            continue
        bar_h = int((prob / 100.0) * height)
        if bar_h <= 0:
            continue
        bar_x = int(i * step + (step - bar_w) / 2)
        bar_layer.fill(bar_color, rect=pygame.Rect(bar_x, height - bar_h, bar_w, bar_h))
    screen.blit(bar_layer, (x, y))

    rain_pts = []
    for i, (_, prob) in enumerate(points):
        if prob is None:
            continue
        px = int(x + i * step + step / 2)
        py = int(y + height - (prob / 100.0) * height)
        rain_pts.append((px, py))
    if len(rain_pts) >= 2:
        pygame.draw.lines(screen, (140, 180, 240), False, rain_pts, 2)
        pygame.draw.aalines(screen, (140, 180, 240), False, rain_pts)

    temp_pts = []
    for i, (t, _) in enumerate(points):
        if t is None:
            continue
        px = int(x + i * step + step / 2)
        py = int(y + height - ((t - t_min) / (t_max - t_min)) * height)
        temp_pts.append((px, py))
    if len(temp_pts) >= 2:
        pygame.draw.lines(screen, (245, 245, 245), False, temp_pts, 3)
        pygame.draw.aalines(screen, (245, 245, 245), False, temp_pts)

    if not pygame.font.get_init():
        pygame.font.init()
    tick_font = pygame.font.SysFont('dejavusans', 11)
    label_color = (200, 200, 200)
    label_y = y + height + 2
    half = (n - 1) // 2
    last = n - 1
    for label, idx in [('Now', 0), ('+12h', half), ('+{}h'.format(last), last)]:
        rendered = tick_font.render(label, True, label_color)
        lx = int(x + idx * step + step / 2 - rendered.get_width() / 2)
        lx = max(x, min(lx, x + width - rendered.get_width()))
        screen.blit(rendered, (lx, label_y))

    # Top-left: peak rain probability over the window.
    range_font = pygame.font.SysFont('dejavusans', 11)
    valid_probs = [p for _, p in points if p is not None]
    if valid_probs:
        peak = max(valid_probs)
        peak_text = '☔ {}%'.format(int(peak))
        rendered = range_font.render(peak_text, True, (140, 180, 240))
        screen.blit(rendered, (x + 2, y + 2))
    return True


def render_overlay(screen, cache, now):
    dim = pygame.Surface(DISPLAY_SIZE)
    dim.fill((0, 0, 0))
    dim.set_alpha(205)
    screen.blit(dim, (0, 0))

    white = (245, 245, 245)
    yellow = (220, 200, 80)

    if not pygame.font.get_init():
        pygame.font.init()

    temp_font = pygame.font.SysFont('dejavusans', 84, bold=True)
    cond_font = pygame.font.SysFont('dejavusans', 26)
    small_font = pygame.font.SysFont('dejavusans', 20)
    stale_font = pygame.font.SysFont('dejavusans', 14)

    cx = DISPLAY_SIZE[0] // 2

    def draw_centered(surface, font, text, color, y):
        rendered = font.render(text, True, color)
        rect = rendered.get_rect(center=(cx, y))
        surface.blit(rendered, rect)

    # Round-display safe zone is ~440 px diameter centered at (240, 240).
    # The top of the visible circle is narrower than the middle, so the chart
    # sits LOWER (y=70..130) and is NARROWER (x=110..370) than the display
    # rectangle. All other rows are kept inside their respective y-row safe
    # widths.
    chart_x = 110
    chart_y = 70
    chart_w = DISPLAY_SIZE[0] - 2 * chart_x
    chart_h = 60
    render_forecast_chart(screen, cache, now, chart_x, chart_y, chart_w, chart_h)

    if cache and is_weather_stale(cache, WEATHER_REFRESH_MIN, now):
        fetched = cache.get('fetched_at')
        if fetched is not None:
            label = '⚠ stale ' + fetched.strftime('%H:%M')
            draw_centered(screen, stale_font, label, yellow, 50)

    if not cache:
        draw_centered(screen, temp_font, '—', white, 190)
        draw_centered(screen, cond_font, 'loading…', white, 260)
        return

    # Big current temp, centered.
    temp_surface = temp_font.render(_format_temp(cache.get('temp_c')), True, white)
    temp_rect = temp_surface.get_rect(center=(cx, 190))
    screen.blit(temp_surface, temp_rect)

    # Mini ↑max / ↓min stack to the right of the current temp.
    rng = _get_temp_range(cache, now)
    if rng is not None:
        rng_min, rng_max = rng
        rng_font = pygame.font.SysFont('dejavusans', 22)
        rng_color = (200, 200, 200)
        rng_x = temp_rect.right + 6
        max_surface = rng_font.render('↑ {}°'.format(rng_max), True, rng_color)
        min_surface = rng_font.render('↓ {}°'.format(rng_min), True, rng_color)
        screen.blit(max_surface, (rng_x, temp_rect.centery - max_surface.get_height() - 2))
        screen.blit(min_surface, (rng_x, temp_rect.centery + 2))

    draw_centered(screen, cond_font, cache.get('condition', '—'), white, 258)
    draw_centered(screen, small_font, _format_wind(cache.get('wind_kmh')), white, 290)
    draw_centered(screen, small_font, _format_sun(cache.get('sunrise'), cache.get('sunset')), white, 320)
    draw_centered(screen, small_font, _format_rain('Today', cache.get('rain_today', (None, None))), white, 355)
    draw_centered(screen, small_font, _format_rain('Tomorrow', cache.get('rain_tomorrow', (None, None))), white, 390)


def get_epic_images_json():
    # Call the epic api
    response = requests.get("https://epic.gsfc.nasa.gov/api/natural")
    imjson = response.json()
    return imjson


def create_image_urls(photos):
    urls = []
    for photo in photos:
        dt = datetime.datetime.strptime(photo["date"], "%Y-%m-%d %H:%M:%S")
        imageurl = (
            "https://epic.gsfc.nasa.gov/archive/natural/"
            + str(dt.year)
            + "/"
            + str(dt.month).zfill(2)
            + "/"
            + str(dt.day).zfill(2)
            + "/jpg/"
            + photo["image"]
            + ".jpg"
        )
        urls.append(imageurl)
    return urls


def soften_edge_chroma(arr, strength=None, thresh=None):
    import numpy as np

    if strength is None:
        strength = EDGE_DESAT_STRENGTH
    if thresh is None:
        thresh = EDGE_DESAT_THRESH
    if strength <= 0:
        return arr
    a = arr.astype(np.float32)
    y = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    gy, gx = np.gradient(y)
    w = np.clip(np.hypot(gx, gy) / thresh, 0.0, 1.0)
    pad = np.pad(w, 1, mode='edge')
    h, ww = w.shape
    w = np.maximum.reduce([pad[i : i + h, j : j + ww] for i in range(3) for j in range(3)])
    factor = (1.0 - strength * w)[:, :, None]
    out = y[:, :, None] + (a - y[:, :, None]) * factor
    return np.clip(out, 0, 255).astype(np.uint8)


def _apply_edge_desat(surface):
    if EDGE_DESAT_STRENGTH <= 0:
        return surface
    try:
        import numpy  # noqa: F401
    except ImportError:
        return surface
    return pygame.surfarray.make_surface(soften_edge_chroma(pygame.surfarray.array3d(surface)))


def apply_white_balance(arr, gains=None):
    import numpy as np

    if gains is None:
        gains = RGB_GAIN
    if tuple(gains) == (1.0, 1.0, 1.0):
        return arr
    a = arr.astype(np.float32)
    a[:, :, 0] *= gains[0]
    a[:, :, 1] *= gains[1]
    a[:, :, 2] *= gains[2]
    return np.clip(a, 0, 255).astype(np.uint8)


def apply_saturation(arr, sat=None):
    import numpy as np

    if sat is None:
        sat = SATURATION
    if sat == 1.0:
        return arr
    a = arr.astype(np.float32)
    y = (0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2])[:, :, None]
    return np.clip(y + (a - y) * sat, 0, 255).astype(np.uint8)


def _color_correct(surface):
    if RGB_GAIN == (1.0, 1.0, 1.0) and SATURATION == 1.0:
        return surface
    try:
        import numpy  # noqa: F401
    except ImportError:
        return surface
    arr = apply_saturation(apply_white_balance(pygame.surfarray.array3d(surface)))
    return pygame.surfarray.make_surface(arr)


_img_cache = {}


def _load_image(idx):
    surf = _img_cache.get(idx)
    if surf is None:
        surf = _color_correct(pygame.image.load('./' + str(idx) + '.png'))
        _img_cache[idx] = surf
    return surf


def save_photos(imageurls, screen=None):
    print("saving photos")
    counter = 0
    for imageurl in imageurls:
        # Create a surface object, draw image on it..
        image_file = io.BytesIO(urlopen(imageurl).read())
        image = pygame.image.load(image_file)

        # Crop out the centre 830px square from the image to make globe fill screen
        cropped = pygame.Surface((CROP_SIZE, CROP_SIZE))
        cropped.blit(image, (0, 0), (CROP_OFFSET, CROP_OFFSET, CROP_SIZE, CROP_SIZE))
        cropped = pygame.transform.smoothscale(cropped, DISPLAY_SIZE)
        cropped = _apply_edge_desat(cropped)

        pygame.image.save(cropped, "./" + str(counter) + ".png")
        counter += 1
    _img_cache.clear()
    print("photos saved")


def _is_windowed_dev_mode():
    """True when EPIC_WINDOWED is set, or when running on Windows/macOS where
    fullscreen at 480x480 looks bad. The Raspberry Pi (Linux) keeps the
    default fullscreen behavior."""
    if os.environ.get("EPIC_WINDOWED"):
        return True
    return sys.platform in ("win32", "darwin")


def _open_fb(path):
    """Open a Linux framebuffer device, query its dimensions/bpp/stride,
    and mmap its memory. Returns a dict with 'mm', 'xres', 'yres', 'bpp',
    'line_length'. Linux-only — imports fcntl/mmap lazily."""
    import fcntl
    import mmap

    fd = open(path, "r+b", buffering=0)
    fix_buf = bytearray(80)
    fcntl.ioctl(fd.fileno(), FBIOGET_FSCREENINFO, fix_buf)
    smem_len = struct.unpack("I", bytes(fix_buf[20:24]))[0]
    line_length = struct.unpack("I", bytes(fix_buf[44:48]))[0]

    var_buf = bytearray(160)
    fcntl.ioctl(fd.fileno(), FBIOGET_VSCREENINFO, var_buf)
    xres, yres = struct.unpack("II", bytes(var_buf[:8]))
    bpp = struct.unpack("I", bytes(var_buf[24:28]))[0]

    mm = mmap.mmap(fd.fileno(), smem_len, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    return {"fd": fd, "mm": mm, "xres": xres, "yres": yres, "bpp": bpp, "line_length": line_length}


def pack_rgb565(arr):
    """Pack an (H, W, 3) uint8 RGB array into an (H, W) uint16 RGB565 array with
    4x4 ordered (Bayer) dithering. Pure: numpy in, numpy out. Each channel uses a
    spatially offset copy of the Bayer matrix so the dither reads as neutral
    luminance grain instead of the chromatic speckle a shared pattern produces."""
    import numpy as np

    h, w = arr.shape[0], arr.shape[1]
    bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.int16)
    d = np.tile(bayer, ((h + 3) // 4, (w + 3) // 4))[:h, :w]
    dr, dg, db = d, (d + 5) % 16, (d + 10) % 16
    c = arr.astype(np.int16)
    r = np.clip(c[:, :, 0] + (dr >> 1), 0, 255).astype(np.uint16) >> 3
    g = np.clip(c[:, :, 1] + (dg >> 2), 0, 255).astype(np.uint16) >> 2
    b = np.clip(c[:, :, 2] + (db >> 1), 0, 255).astype(np.uint16) >> 3
    return (r << 11) | (g << 5) | b


def _push_to_fb(surface, fb):
    """Copy a pygame Surface into a mmapped framebuffer, converting pixel
    format on the fly. Supports 16bpp RGB565 (numpy required, dithered) and 32bpp BGRA."""
    if fb["bpp"] == 32:
        data = pygame.image.tobytes(surface, "BGRA")
        # Some fbs have stride padding; copy row by row if so.
        row = fb["xres"] * 4
        if fb["line_length"] == row:
            fb["mm"][: len(data)] = data
        else:
            for y in range(fb["yres"]):
                fb["mm"][y * fb["line_length"] : y * fb["line_length"] + row] = data[y * row : (y + 1) * row]
    elif fb["bpp"] == 16:
        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("16bpp framebuffer requires numpy: pip install numpy")
        arr = pygame.surfarray.array3d(surface).swapaxes(0, 1)  # (H, W, 3) uint8
        data = np.ascontiguousarray(pack_rgb565(arr)).tobytes()
        row = fb["xres"] * 2
        if fb["line_length"] == row:
            fb["mm"][: len(data)] = data
        else:
            for y in range(fb["yres"]):
                fb["mm"][y * fb["line_length"] : y * fb["line_length"] + row] = data[y * row : (y + 1) * row]
    else:
        raise RuntimeError("Unsupported bpp: " + str(fb["bpp"]))


def _present(surface):
    """Push the given surface to the active framebuffer if EPIC_FBDEV mode is on,
    then call pygame.display.flip(). Safe to call when _FB is None."""
    global _FB
    if _FB is not None:
        _push_to_fb(surface, _FB)
    pygame.display.flip()


def _set_backlight(on):
    """Best-effort backlight toggle via `pinctrl`. Returns True only when the
    command ran and exited 0. No-op (False) when EPIC_NO_BACKLIGHT_CTL is set or
    pinctrl is missing/fails — the black night frame is the guaranteed fallback."""
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


def _install_sigusr1_tap():
    """Wire SIGUSR1 to inject a MOUSEBUTTONDOWN event. Lets you toggle the
    overlay from SSH with `pkill -USR1 -f epic.py`. POSIX only."""
    try:
        import signal
    except ImportError:
        return

    if not hasattr(signal, "SIGUSR1"):
        return

    def _handler(_signum, _frame):
        try:
            pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0)))
        except pygame.error:
            pass

    signal.signal(signal.SIGUSR1, _handler)


def _start_evdev_touch_reader():
    """Read /dev/input/eventN directly and inject pygame MOUSEBUTTONDOWN events.

    Required when running with SDL_VIDEODRIVER=dummy (EPIC_FBDEV mode), because
    the dummy backend doesn't poll input devices.

    Filtering — designed to reject ft5x06 ghost-burst noise on the Hyperpixel
    Round panel:
      * Requires a 'quiet period' before accepting a press: any prior touch
        event resets a quiet-watchdog. A press is treated as real only if no
        events have arrived for at least EPIC_TOUCH_QUIET_MS (default 800ms).
      * Then debounces consecutive accepted presses by EPIC_TOUCH_DEBOUNCE_MS
        (default 350ms).
      * Set EPIC_NO_TOUCH=1 to skip the reader entirely (rely on keyboard
        events from another source, or no input at all).
      * Set EPIC_TOUCH_DEV to override the device path (default /dev/input/event0)."""
    if os.environ.get("EPIC_NO_TOUCH"):
        print("touch reader disabled via EPIC_NO_TOUCH")
        return

    import struct

    path = os.environ.get("EPIC_TOUCH_DEV", "/dev/input/event0")
    debounce_s = int(os.environ.get("EPIC_TOUCH_DEBOUNCE_MS", "350")) / 1000.0
    quiet_s = int(os.environ.get("EPIC_TOUCH_QUIET_MS", "800")) / 1000.0

    fmt = "llHHi"
    size = struct.calcsize(fmt)
    EV_KEY = 0x01
    BTN_TOUCH = 0x14A
    BTN_LEFT = 0x110

    def _reader():
        try:
            fp = open(path, "rb", buffering=0)
        except Exception as exc:
            print("touch reader could not open " + path + ":", exc)
            return
        last_event_at = 0.0
        last_press_at = 0.0
        while True:
            try:
                data = fp.read(size)
                if not data or len(data) < size:
                    continue
                _sec, _usec, type_, code, value = struct.unpack(fmt, data)
                now_t = time.time()
                # Track ANY event so noise activity resets the quiet window.
                quiet_long_enough = (now_t - last_event_at) >= quiet_s
                last_event_at = now_t

                if type_ != EV_KEY or value != 1:
                    continue
                if code != BTN_TOUCH and code != BTN_LEFT:
                    continue
                if not quiet_long_enough:
                    continue
                if now_t - last_press_at < debounce_s:
                    continue
                last_press_at = now_t
                try:
                    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0)))
                except pygame.error:
                    pass
            except Exception as exc:
                print("touch reader error:", exc)
                time.sleep(0.5)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()


def init_display():
    """Initialize pygame and create display surface.

    Three modes:
      * EPIC_FBDEV=/dev/fbN -> render to in-memory surface, flip pushes to fb
        directly (no SDL backend needed). Best for headless Pi with stripped SDL.
      * EPIC_WINDOWED=1 (or win32/darwin) -> windowed dev mode.
      * Otherwise -> SDL fullscreen.
    """
    global _FB
    fbdev = os.environ.get("EPIC_FBDEV")
    if fbdev:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.init()
        pygame.display.init()
        # Open the fb first so we can fail fast on permission/format issues.
        _FB = _open_fb(fbdev)
        # SDL's dummy backend doesn't read input. Spawn a thread that watches the
        # touch device and injects MOUSEBUTTONDOWN events into pygame's queue.
        _start_evdev_touch_reader()
        # Also wire SIGUSR1 so you can toggle the overlay over SSH:
        # `pkill -USR1 -f epic.py` — useful when touch hardware is unreliable.
        _install_sigusr1_tap()
        # The pygame surface we draw into.
        screen = pygame.display.set_mode(list(DISPLAY_SIZE))
        screen.fill((0, 0, 0))
        return screen

    pygame.init()
    if sys.platform.startswith("linux"):
        os.environ["DISPLAY"] = ":0"
    pygame.display.init()
    if _is_windowed_dev_mode():
        screen = pygame.display.set_mode(list(DISPLAY_SIZE))
        pygame.display.set_caption("EPIC (dev — ESC quits)")
    else:
        screen = pygame.display.set_mode(list(DISPLAY_SIZE), pygame.FULLSCREEN)
        pygame.mouse.set_visible(0)
    screen.fill((0, 0, 0))
    return screen


def _maybe_kick_tap_refresh(lat, lon, cache_ref, lock):
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
        except Exception as exc:
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


def _maybe_check_for_new_images(state, screen, now, force=False):
    if not force and now < state.next_image_api_check_at:
        return state
    print(str(now) + ' Checking for new images.')
    try:
        image_data = get_epic_images_json()
    except Exception as exc:
        print('image API check failed:', exc)
        return replace(state, next_image_api_check_at=now + datetime.timedelta(minutes=check_delay))
    newest = image_data[0]['date'] if image_data else ''
    if newest and (force or newest != state.last_image_data):
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


def build_test_pattern(size=DISPLAY_SIZE):
    import numpy as np

    w, h = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    band = h // 3
    ramp = (np.arange(w) * 255 // max(w - 1, 1)).astype(np.uint8)
    img[0:band] = ramp[None, :, None]
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255), (255, 255, 0)]
    bw = w // len(bars)
    for i, c in enumerate(bars):
        img[band : 2 * band, i * bw : (i + 1) * bw] = c
    img[band : 2 * band, len(bars) * bw :] = bars[-1]
    steps = [0, 64, 128, 192, 255]
    sw = w // len(steps)
    for i, v in enumerate(steps):
        img[2 * band :, i * sw : (i + 1) * sw] = v
    img[2 * band :, len(steps) * sw :] = steps[-1]
    return img


def _run_test_pattern(screen):
    try:
        import numpy  # noqa: F401
    except ImportError:
        print('EPIC_TESTPATTERN needs numpy; skipping')
        return
    surf = pygame.surfarray.make_surface(build_test_pattern(DISPLAY_SIZE).swapaxes(0, 1))
    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                return
        screen.blit(surf, (0, 0))
        _present(screen)
        clock.tick(10)


def main():
    lat, lon, display_name = geocode_city(CITY_NAME)
    print('Weather for: ' + display_name + ' (' + str(lat) + ', ' + str(lon) + ')')

    screen = init_display()

    if TEST_PATTERN:
        _run_test_pattern(screen)
        return

    try:
        loading = pygame.image.load(r'./loading.jpg')
        screen.blit(loading, (0, 0))
        _present(screen)
    except Exception as exc:
        print('loading splash skipped:', exc)

    print('Checking for new photos every ' + str(check_delay) + ' minutes')
    print('Rotating photos every ' + str(rotate_delay) + ' seconds')

    weather_cache_ref = {'value': None, 'inflight': False}
    weather_lock = threading.Lock()

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
        next_image_api_check_at=now,
        overlay_dismiss_at=None,
        blend_started_at=None,
        last_image_data='',
    )

    on_t = _parse_clock(SCREEN_ON)
    off_t = _parse_clock(SCREEN_OFF)
    black_frame = pygame.Surface(DISPLAY_SIZE)
    black_frame.fill((0, 0, 0))
    screen_on = True

    api_bridge = epic_api.ApiBridge()
    if not os.environ.get('EPIC_API_DISABLE'):
        epic_api.start_api_server(api_bridge, API_HOST, API_PORT)
    screen_override = 'auto'
    force_image_refresh = False

    clock = pygame.time.Clock()
    running = True

    while running:
        now = datetime.datetime.now()
        events = pygame.event.get()

        for event in events:
            if event.type == pygame.QUIT:
                running = False
                break
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
                break
        if not running:
            break

        for cmd in api_bridge.drain_commands():
            if cmd['cmd'] == 'overlay':
                state = apply_overlay_command(state, cmd['action'], now)
            elif cmd['cmd'] == 'screen':
                screen_override = cmd['action']
            elif cmd['cmd'] == 'refresh_image':
                force_image_refresh = True

        scheduled_on = (not NIGHT_MODE) or is_screen_on(now, on_t, off_t)
        now_on = resolve_screen_on(screen_override, scheduled_on)
        edge = night_transition(screen_on, now_on)
        if edge == 'sleep':
            _set_backlight(False)
            state = replace(state, mode=MODE_PHOTO, overlay_dismiss_at=None)
            screen.blit(black_frame, (0, 0))
            _present(screen)
        elif edge == 'wake':
            _set_backlight(True)
        screen_on = now_on

        with weather_lock:
            _weather_snapshot = weather_cache_ref.get('value')
        api_bridge.set_status(build_status(state, _weather_snapshot, now_on, screen_override, on_t, off_t))

        if not now_on:
            clock.tick(4)
            continue

        if any(e.type == pygame.MOUSEBUTTONDOWN for e in events):
            _maybe_kick_tap_refresh(lat, lon, weather_cache_ref, weather_lock)

        state = _maybe_check_for_new_images(state, screen, now, force=force_image_refresh)
        force_image_refresh = False

        state = tick_state(
            state,
            events,
            now,
            blend_enabled=enable_blending,
            rotate_delay=rotate_delay,
            blend_duration=blending_duration,
        )

        if state.num_photos > 0:
            current_img = _load_image(state.current_idx)
            if state.mode == MODE_BLENDING:
                prev_idx = (state.current_idx - 1) % state.num_photos
                old_img = _load_image(prev_idx)
                alpha = compute_blend_alpha(now, state.blend_started_at, blending_duration)
                render_blend(screen, old_img, current_img, alpha)
            else:
                render_photo(screen, current_img)

            if state.mode == MODE_OVERLAY:
                with weather_lock:
                    cache_snapshot = weather_cache_ref.get('value')
                render_overlay(screen, cache_snapshot, now)

        _present(screen)
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
