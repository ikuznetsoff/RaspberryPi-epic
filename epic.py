import datetime
import io
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from urllib.request import urlopen

import pygame
import requests

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

DISPLAY_SIZE = (480, 480)
CROP_SIZE = 830
CROP_OFFSET = 125

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


def is_weather_stale(cache, refresh_min, now):
    if not cache:
        return True
    fetched_at = cache.get('fetched_at')
    if fetched_at is None:
        return True
    return (now - fetched_at) > datetime.timedelta(minutes=refresh_min)


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

    pts = []
    for i, (t, _) in enumerate(points):
        if t is None:
            continue
        px = int(x + i * step + step / 2)
        py = int(y + height - ((t - t_min) / (t_max - t_min)) * height)
        pts.append((px, py))
    if len(pts) >= 2:
        pygame.draw.aalines(screen, (245, 245, 245), False, pts, 2)

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

    # Range label — placed inside the chart area (top-right) so it stays
    # within the round-bezel safe zone on the Hyperpixel display.
    range_font = pygame.font.SysFont('dejavusans', 11)
    range_text = '{}°/{}°'.format(int(round(t_max)), int(round(t_min)))
    rendered = range_font.render(range_text, True, label_color)
    screen.blit(rendered, (x + width - rendered.get_width() - 2, y + 2))
    return True


def render_overlay(screen, cache, now):
    dim = pygame.Surface(DISPLAY_SIZE)
    dim.fill((0, 0, 0))
    dim.set_alpha(180)
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
        draw_centered(screen, temp_font, '—', white, 215)
        draw_centered(screen, cond_font, 'loading…', white, 290)
        return

    draw_centered(screen, temp_font, _format_temp(cache.get('temp_c')), white, 215)
    draw_centered(screen, cond_font, cache.get('condition', '—'), white, 285)
    draw_centered(screen, small_font, _format_wind(cache.get('wind_kmh')), white, 320)
    draw_centered(screen, small_font, _format_sun(cache.get('sunrise'), cache.get('sunset')), white, 350)
    draw_centered(screen, small_font, _format_rain('Today', cache.get('rain_today', (None, None))), white, 385)
    draw_centered(screen, small_font, _format_rain('Tomorrow', cache.get('rain_tomorrow', (None, None))), white, 415)


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
        cropped = pygame.transform.scale(cropped, DISPLAY_SIZE)

        pygame.image.save(cropped, "./" + str(counter) + ".jpg")
        counter += 1
    print("photos saved")


def _is_windowed_dev_mode():
    """True when EPIC_WINDOWED is set, or when running on Windows/macOS where
    fullscreen at 480x480 looks bad. The Raspberry Pi (Linux) keeps the
    default fullscreen behavior."""
    if os.environ.get("EPIC_WINDOWED"):
        return True
    return sys.platform in ("win32", "darwin")


def init_display():
    """Initialize pygame and create display surface."""
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


def _maybe_check_for_new_images(state, screen, now):
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
    lat, lon, display_name = geocode_city(CITY_NAME)
    print('Weather for: ' + display_name + ' (' + str(lat) + ', ' + str(lon) + ')')

    screen = init_display()

    try:
        loading = pygame.image.load(r'./loading.jpg')
        screen.blit(loading, (0, 0))
        pygame.display.flip()
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

        if any(e.type == pygame.MOUSEBUTTONDOWN for e in events):
            _maybe_kick_tap_refresh(lat, lon, weather_cache_ref, weather_lock)

        state = _maybe_check_for_new_images(state, screen, now)

        state = tick_state(
            state,
            events,
            now,
            blend_enabled=enable_blending,
            rotate_delay=rotate_delay,
            blend_duration=blending_duration,
        )

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


if __name__ == "__main__":
    main()
