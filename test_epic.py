"""Tests for epic.py — DSCOVR EPIC Image Viewer."""

import datetime
import io
import json
import os
import tempfile
from unittest import mock

import pytest

# We must initialize pygame minimally before importing epic
# since epic.py imports pygame at module level
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

pygame.init()

import epic
import epic_api

# ============================================================
# Settings / defaults
# ============================================================


class TestDefaultSettings:
    def test_check_delay_default(self):
        assert epic.check_delay == 120

    def test_rotate_delay_default(self):
        assert epic.rotate_delay == 20

    def test_enable_blending_default(self):
        assert epic.enable_blending is True

    def test_blending_duration_default(self):
        assert epic.blending_duration == 5

    def test_display_size(self):
        assert epic.DISPLAY_SIZE == (480, 480)

    def test_crop_size(self):
        assert epic.CROP_SIZE == 830

    def test_crop_offset(self):
        assert epic.CROP_OFFSET == 125


# ============================================================
# create_image_urls
# ============================================================


class TestCreateImageUrls:
    def test_single_photo(self):
        photos = [{"date": "2025-03-14 12:30:00", "image": "epic_1b_20250314123000"}]
        urls = epic.create_image_urls(photos)
        assert len(urls) == 1
        assert urls[0] == "https://epic.gsfc.nasa.gov/archive/natural/2025/03/14/jpg/epic_1b_20250314123000.jpg"

    def test_multiple_photos(self):
        photos = [
            {"date": "2025-01-02 06:00:00", "image": "img_a"},
            {"date": "2025-12-31 23:59:59", "image": "img_b"},
        ]
        urls = epic.create_image_urls(photos)
        assert len(urls) == 2
        assert "/2025/01/02/" in urls[0]
        assert "/2025/12/31/" in urls[1]

    def test_empty_list(self):
        assert epic.create_image_urls([]) == []

    def test_date_zero_padding(self):
        photos = [{"date": "2025-01-09 01:02:03", "image": "x"}]
        urls = epic.create_image_urls(photos)
        assert "/01/09/" in urls[0]

    def test_url_contains_image_name(self):
        photos = [{"date": "2025-06-15 12:00:00", "image": "my_unique_image_name"}]
        urls = epic.create_image_urls(photos)
        assert "my_unique_image_name.jpg" in urls[0]

    def test_url_starts_with_https(self):
        photos = [{"date": "2025-06-15 12:00:00", "image": "test"}]
        urls = epic.create_image_urls(photos)
        assert urls[0].startswith("https://")

    def test_url_ends_with_jpg(self):
        photos = [{"date": "2025-06-15 12:00:00", "image": "test"}]
        urls = epic.create_image_urls(photos)
        assert urls[0].endswith(".jpg")


# ============================================================
# get_epic_images_json
# ============================================================


class TestGetEpicImagesJson:
    @mock.patch("epic.requests.get")
    def test_returns_json(self, mock_get):
        mock_resp = mock.Mock()
        mock_resp.json.return_value = [{"date": "2025-03-14 12:00:00", "image": "test"}]
        mock_get.return_value = mock_resp
        result = epic.get_epic_images_json()
        assert isinstance(result, list)
        assert len(result) == 1

    @mock.patch("epic.requests.get")
    def test_calls_correct_url(self, mock_get):
        mock_resp = mock.Mock()
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp
        epic.get_epic_images_json()
        mock_get.assert_called_once_with("https://epic.gsfc.nasa.gov/api/natural")

    @mock.patch("epic.requests.get")
    def test_empty_response(self, mock_get):
        mock_resp = mock.Mock()
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp
        result = epic.get_epic_images_json()
        assert result == []

    @mock.patch("epic.requests.get")
    def test_multiple_images(self, mock_get):
        data = [
            {"date": "2025-03-14 10:00:00", "image": "a"},
            {"date": "2025-03-14 11:00:00", "image": "b"},
            {"date": "2025-03-14 12:00:00", "image": "c"},
        ]
        mock_resp = mock.Mock()
        mock_resp.json.return_value = data
        mock_get.return_value = mock_resp
        result = epic.get_epic_images_json()
        assert len(result) == 3


# ============================================================
# save_photos
# ============================================================


class TestSavePhotos:
    @mock.patch("epic.urlopen")
    def test_saves_correct_number_of_files(self, mock_urlopen, tmp_path):
        # Create a small pygame surface to serve as the downloaded image
        surf = pygame.Surface((1080, 1080))
        buf = io.BytesIO()
        pygame.image.save(surf, buf, "test.bmp")
        buf.seek(0)

        mock_urlopen.return_value = buf
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            epic.save_photos(["http://example.com/test.jpg"])
            assert os.path.exists("0.jpg")
        finally:
            os.chdir(old_cwd)

    @mock.patch("epic.urlopen")
    def test_counter_increments(self, mock_urlopen, tmp_path):
        surf = pygame.Surface((1080, 1080))

        def make_buf(*args, **kwargs):
            buf = io.BytesIO()
            pygame.image.save(surf, buf, "test.bmp")
            buf.seek(0)
            return buf

        mock_urlopen.side_effect = make_buf
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            epic.save_photos(["http://a.jpg", "http://b.jpg", "http://c.jpg"])
            assert os.path.exists("0.jpg")
            assert os.path.exists("1.jpg")
            assert os.path.exists("2.jpg")
        finally:
            os.chdir(old_cwd)

    @mock.patch("epic.urlopen")
    def test_saved_image_dimensions(self, mock_urlopen, tmp_path):
        surf = pygame.Surface((1080, 1080))
        buf = io.BytesIO()
        pygame.image.save(surf, buf, "test.bmp")
        buf.seek(0)
        mock_urlopen.return_value = buf
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            epic.save_photos(["http://example.com/test.jpg"])
            loaded = pygame.image.load("0.jpg")
            assert loaded.get_size() == epic.DISPLAY_SIZE
        finally:
            os.chdir(old_cwd)

    @mock.patch("epic.urlopen")
    def test_empty_urls(self, mock_urlopen, tmp_path):
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            epic.save_photos([])
            assert not os.path.exists("0.jpg")
        finally:
            os.chdir(old_cwd)


# ============================================================
# init_display
# ============================================================


class TestInitDisplay:
    @mock.patch("epic.pygame.display.set_mode")
    @mock.patch("epic.pygame.display.init")
    @mock.patch("epic.pygame.init")
    @mock.patch("epic.pygame.mouse.set_visible")
    def test_init_display_fullscreen_path(self, mock_visible, mock_init, mock_dinit, mock_setmode):
        fake_screen = mock.Mock()
        mock_setmode.return_value = fake_screen
        with mock.patch("epic._is_windowed_dev_mode", return_value=False):
            result = epic.init_display()
        mock_init.assert_called_once()
        mock_setmode.assert_called_once()
        # Fullscreen path hides the mouse cursor.
        mock_visible.assert_called_once_with(0)
        assert result == fake_screen

    @mock.patch("epic.pygame.display.set_mode")
    @mock.patch("epic.pygame.display.init")
    @mock.patch("epic.pygame.init")
    @mock.patch("epic.pygame.mouse.set_visible")
    def test_init_display_windowed_dev_path(self, mock_visible, mock_init, mock_dinit, mock_setmode):
        fake_screen = mock.Mock()
        mock_setmode.return_value = fake_screen
        with mock.patch("epic._is_windowed_dev_mode", return_value=True):
            with mock.patch("epic.pygame.display.set_caption") as mock_caption:
                result = epic.init_display()
        # Windowed path keeps mouse cursor visible (no set_visible call) and sets a caption.
        mock_visible.assert_not_called()
        mock_caption.assert_called_once()
        assert result == fake_screen

    @mock.patch("epic.pygame.display.set_mode")
    @mock.patch("epic.pygame.display.init")
    @mock.patch("epic.pygame.init")
    @mock.patch("epic.pygame.mouse.set_visible")
    def test_screen_filled_black(self, mock_visible, mock_init, mock_dinit, mock_setmode):
        fake_screen = mock.Mock()
        mock_setmode.return_value = fake_screen
        with mock.patch("epic._is_windowed_dev_mode", return_value=False):
            epic.init_display()
        fake_screen.fill.assert_called_once_with((0, 0, 0))

    def test_is_windowed_dev_mode_env_var(self, monkeypatch):
        monkeypatch.setenv("EPIC_WINDOWED", "1")
        assert epic._is_windowed_dev_mode() is True

    def test_is_windowed_dev_mode_linux(self, monkeypatch):
        monkeypatch.delenv("EPIC_WINDOWED", raising=False)
        monkeypatch.setattr(epic.sys, "platform", "linux")
        assert epic._is_windowed_dev_mode() is False

    def test_is_windowed_dev_mode_windows(self, monkeypatch):
        monkeypatch.delenv("EPIC_WINDOWED", raising=False)
        monkeypatch.setattr(epic.sys, "platform", "win32")
        assert epic._is_windowed_dev_mode() is True


# ============================================================
# Module import safety
# ============================================================


class TestModuleImport:
    def test_import_does_not_start_main_loop(self):
        """Importing epic should not start the main loop (guarded by __name__)."""
        # If we got this far, the import succeeded without hanging
        assert hasattr(epic, "main")
        assert hasattr(epic, "get_epic_images_json")
        assert hasattr(epic, "create_image_urls")
        assert hasattr(epic, "save_photos")
        assert hasattr(epic, "tick_state")
        assert hasattr(epic, "render_overlay")
        assert hasattr(epic, "init_display")

    def test_module_has_settings(self):
        assert hasattr(epic, "check_delay")
        assert hasattr(epic, "rotate_delay")
        assert hasattr(epic, "enable_blending")
        assert hasattr(epic, "blending_duration")

    def test_main_function_exists(self):
        """main() should be callable."""
        assert callable(epic.main)


# ============================================================
# save_photos — additional edge cases
# ============================================================


class TestSavePhotosAdvanced:
    @mock.patch("epic.urlopen")
    def test_save_with_screen(self, mock_urlopen, tmp_path):
        """save_photos with screen should display images as they download."""
        surf = pygame.Surface((1080, 1080))
        buf = io.BytesIO()
        pygame.image.save(surf, buf, "test.bmp")
        buf.seek(0)
        mock_urlopen.return_value = buf

        screen = pygame.Surface((480, 480))
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with mock.patch("epic.pygame.display.flip"):
                epic.save_photos(["http://example.com/test.jpg"], screen)
            assert os.path.exists("0.jpg")
        finally:
            os.chdir(old_cwd)


# ============================================================
# create_image_urls — edge cases
# ============================================================


class TestCreateImageUrlsEdgeCases:
    def test_special_characters_in_image_name(self):
        photos = [{"date": "2025-06-15 12:00:00", "image": "epic_1b_20250615"}]
        urls = epic.create_image_urls(photos)
        assert "epic_1b_20250615.jpg" in urls[0]

    def test_preserves_order(self):
        photos = [
            {"date": "2025-01-01 00:00:00", "image": "first"},
            {"date": "2025-12-31 23:59:59", "image": "last"},
        ]
        urls = epic.create_image_urls(photos)
        assert "first" in urls[0]
        assert "last" in urls[1]

    def test_single_digit_month_and_day(self):
        photos = [{"date": "2025-01-01 01:01:01", "image": "x"}]
        urls = epic.create_image_urls(photos)
        assert "/01/01/" in urls[0]


# ============================================================
# get_epic_images_json — error handling
# ============================================================


class TestGetEpicImagesJsonErrors:
    @mock.patch("epic.requests.get")
    def test_network_error(self, mock_get):
        """Should propagate network errors."""
        mock_get.side_effect = Exception("Network error")
        with pytest.raises(Exception, match="Network error"):
            epic.get_epic_images_json()


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
        kwargs = get.call_args.kwargs
        url = get.call_args.args[0]
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


# ============================================================
# fetch_weather
# ============================================================


class TestFetchWeather:
    def _payload(self, **overrides):
        base = {
            'current': {
                'temperature_2m': -7.6,
                'weather_code': 2,
                'wind_speed_10m': 14.3,
            },
            'hourly': {
                'time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)]
                + ['2026-05-03T{:02d}:00'.format(h) for h in range(24)],
                'temperature_2m': [float(h) for h in range(48)],
                'precipitation_probability': [h * 2 for h in range(48)],
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
        assert cache['temp_c'] == -8
        assert cache['weather_code'] == 2
        assert cache['condition'] == 'Partly Cloudy'
        assert cache['wind_kmh'] == 14
        assert cache['sunrise'] == '06:42'
        assert cache['sunset'] == '19:08'
        assert cache['rain_today'] == (60, 2.0)
        assert cache['rain_tomorrow'] == (80, 5.4)
        assert len(cache['hourly_time']) == 48
        assert len(cache['hourly_temp']) == 48
        assert len(cache['hourly_prob']) == 48
        assert isinstance(cache['fetched_at'], datetime.datetime)
        kwargs = get.call_args.kwargs
        assert kwargs['timeout'] == epic.HTTP_TIMEOUT
        assert kwargs['params']['latitude'] == 52.23
        assert kwargs['params']['longitude'] == 21.01
        assert kwargs['params']['forecast_days'] == 2
        assert 'wind_speed_10m' in kwargs['params']['current']
        assert kwargs['params']['wind_speed_unit'] == 'kmh'
        assert 'temperature_2m' in kwargs['params']['hourly']
        assert 'precipitation_probability' in kwargs['params']['hourly']

    def test_hourly_missing_returns_empty_lists(self):
        fake_response = mock.Mock()
        payload = self._payload()
        del payload['hourly']
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['hourly_time'] == []
        assert cache['hourly_temp'] == []
        assert cache['hourly_prob'] == []

    def test_missing_wind(self):
        payload = self._payload()
        payload['current']['wind_speed_10m'] = None
        fake_response = mock.Mock()
        fake_response.json.return_value = payload
        fake_response.raise_for_status = mock.Mock()
        with mock.patch('epic.requests.get', return_value=fake_response):
            cache = epic.fetch_weather(0.0, 0.0)
        assert cache['wind_kmh'] is None

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
        assert epic.is_weather_stale(cache, refresh_min=30, now=now) is False


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
        assert new.current_idx == 0

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
        assert new.current_idx == 2
        assert new.blend_started_at is None


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
        assert screen.get_at((0, 0))[:3] == (0, 255, 0)

    def test_render_blend_clamps_alpha(self):
        screen = self._make_screen()
        old = self._make_image((255, 0, 0))
        new = self._make_image((0, 255, 0))
        epic.render_blend(screen, old, new, alpha=500)
        assert screen.get_at((0, 0))[:3] == (0, 255, 0)
        epic.render_blend(screen, old, new, alpha=-100)
        assert screen.get_at((0, 0))[:3] == (255, 0, 0)

    def test_compute_blend_alpha_start(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        assert epic.compute_blend_alpha(t0, t0, 5) == 0

    def test_compute_blend_alpha_mid(self):
        t0 = datetime.datetime(2026, 5, 2, 12, 0, 0)
        mid = t0 + datetime.timedelta(seconds=2.5)
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
            'wind_kmh': 14,
            'sunrise': '06:42',
            'sunset': '19:08',
            'rain_today': (60, 2.0),
            'rain_tomorrow': (80, 5.4),
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [float(h) for h in range(24)],
            'hourly_prob': [h * 4 for h in range(24)],
            'fetched_at': datetime.datetime(2026, 5, 2, 11, 50),
        }

    def test_format_wind_value(self):
        assert epic._format_wind(14) == 'Wind 14 km/h'

    def test_format_wind_none(self):
        assert epic._format_wind(None) == 'Wind —'

    def test_render_with_full_cache(self):
        screen = self._screen()
        screen.fill((255, 255, 255))
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        epic.render_overlay(screen, self._full_cache(), now)
        cx, cy = 240, 240
        r, g, b = screen.get_at((cx, cy))[:3]
        assert (r, g, b) != (255, 255, 255)

    def test_render_with_no_cache(self):
        screen = self._screen()
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
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
        cache['fetched_at'] = datetime.datetime(2026, 5, 2, 10, 0)
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        epic.render_overlay(screen, cache, now)


# ============================================================
# main loop — smoke
# ============================================================


class TestMainLoopSmoke:
    def test_main_exits_cleanly_on_quit_event(self, monkeypatch, tmp_path):
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
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
            # Don't actually tear down pygame globals — the test module shares them.
            monkeypatch.setattr(epic.pygame, 'quit', lambda: None)
            monkeypatch.setattr(epic.pygame.display, 'flip', lambda: None)

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


# ============================================================
# Tap-driven refresh wiring
# ============================================================


class TestTapRefresh:
    def test_skips_when_fresh(self, monkeypatch):
        cache_ref = {
            'value': {'fetched_at': datetime.datetime.now()},
            'inflight': False,
        }
        lock = mock.MagicMock()
        called = []
        monkeypatch.setattr(epic, 'fetch_weather', lambda lat, lon: called.append((lat, lon)))
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
                'temp_c': 1,
                'weather_code': 0,
                'condition': 'Clear',
                'sunrise': '06:00',
                'sunset': '18:00',
                'rain_today': (0, 0.0),
                'rain_tomorrow': (0, 0.0),
                'fetched_at': datetime.datetime.now(),
            },
        )

        epic._maybe_kick_tap_refresh(52.23, 21.01, cache_ref, lock)

        assert captured.get('target') is not None
        assert cache_ref['value']['temp_c'] == 1
        assert cache_ref['inflight'] is False

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

        epic._maybe_kick_tap_refresh(0.0, 0.0, cache_ref, lock)
        assert cache_ref['value']['fetched_at'] == old
        assert cache_ref['inflight'] is False


# ============================================================
# Coverage gap-fillers
# ============================================================


class TestCoverageGaps:
    def test_safe_index_none_seq(self):
        assert epic._safe_index(None, 0) is None

    def test_parse_hhmm_none(self):
        assert epic._parse_hhmm(None) is None

    def test_parse_hhmm_no_t_separator(self):
        assert epic._parse_hhmm('06:42') == '06:42'

    def test_is_weather_stale_fetched_at_none(self):
        cache = {'fetched_at': None}
        now = datetime.datetime(2026, 5, 2, 12, 0)
        assert epic.is_weather_stale(cache, refresh_min=30, now=now) is True

    def test_tick_state_overlay_no_auto_dismiss_yet(self):
        opened = datetime.datetime(2026, 5, 2, 12, 0, 0)
        state = epic.AppState(
            mode=epic.MODE_OVERLAY,
            current_idx=0,
            num_photos=3,
            next_photo_swap_at=opened,
            next_image_api_check_at=opened,
            overlay_dismiss_at=opened + datetime.timedelta(seconds=60),
            blend_started_at=None,
            last_image_data='',
        )
        now = opened + datetime.timedelta(seconds=10)
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_OVERLAY  # still up

    def test_tick_state_blending_with_none_started(self):
        now = datetime.datetime(2026, 5, 2, 12, 0, 0)
        state = epic.AppState(
            mode=epic.MODE_BLENDING,
            current_idx=0,
            num_photos=3,
            next_photo_swap_at=now,
            next_image_api_check_at=now,
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='',
        )
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        # Defensive: stays blending (blend_started_at sentinel for "not actually started")
        assert new.mode == epic.MODE_BLENDING

    def test_tick_state_blending_mid_progress(self):
        started = datetime.datetime(2026, 5, 2, 12, 0, 0)
        state = epic.AppState(
            mode=epic.MODE_BLENDING,
            current_idx=0,
            num_photos=3,
            next_photo_swap_at=started,
            next_image_api_check_at=started,
            overlay_dismiss_at=None,
            blend_started_at=started,
            last_image_data='',
        )
        now = started + datetime.timedelta(seconds=2)  # mid-blend
        new = epic.tick_state(state, [], now, blend_enabled=True, rotate_delay=20, blend_duration=5)
        assert new.mode == epic.MODE_BLENDING
        assert new.current_idx == 0  # not yet advanced

    def test_render_overlay_initializes_font_when_uninited(self, monkeypatch):
        # Force the "font not init" branch
        monkeypatch.setattr(epic.pygame.font, 'get_init', lambda: False)
        init_calls = []
        original_init = epic.pygame.font.init
        monkeypatch.setattr(epic.pygame.font, 'init', lambda: init_calls.append(1) or original_init())
        screen = pygame.Surface((480, 480))
        now = datetime.datetime(2026, 5, 2, 12, 0)
        epic.render_overlay(screen, None, now)
        assert init_calls  # init was called at least once

    def test_maybe_check_for_new_images_not_due(self):
        now = datetime.datetime(2026, 5, 2, 12, 0)
        state = epic.AppState(
            mode=epic.MODE_PHOTO,
            current_idx=0,
            num_photos=2,
            next_photo_swap_at=now,
            next_image_api_check_at=now + datetime.timedelta(minutes=30),  # not yet
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='abc',
        )
        new = epic._maybe_check_for_new_images(state, mock.Mock(), now)
        assert new is state  # unchanged

    def test_maybe_check_for_new_images_no_new_data(self, monkeypatch):
        now = datetime.datetime(2026, 5, 2, 12, 0)
        state = epic.AppState(
            mode=epic.MODE_PHOTO,
            current_idx=0,
            num_photos=2,
            next_photo_swap_at=now,
            next_image_api_check_at=now,  # due
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='2026-05-02 12:00:00',  # same as what API returns
        )
        monkeypatch.setattr(epic, 'get_epic_images_json', lambda: [{'date': '2026-05-02 12:00:00', 'image': 'x'}])
        save_called = []
        monkeypatch.setattr(epic, 'save_photos', lambda *a, **k: save_called.append(1))
        new = epic._maybe_check_for_new_images(state, mock.Mock(), now)
        assert save_called == []  # no download since data unchanged
        assert new.next_image_api_check_at > now

    def test_maybe_check_for_new_images_api_failure(self, monkeypatch):
        now = datetime.datetime(2026, 5, 2, 12, 0)
        state = epic.AppState(
            mode=epic.MODE_PHOTO,
            current_idx=0,
            num_photos=2,
            next_photo_swap_at=now,
            next_image_api_check_at=now,
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='',
        )

        def boom():
            raise RuntimeError('api down')

        monkeypatch.setattr(epic, 'get_epic_images_json', boom)
        new = epic._maybe_check_for_new_images(state, mock.Mock(), now)
        # Schedule advances even on failure so we don't hammer.
        assert new.next_image_api_check_at > now
        assert new.last_image_data == ''  # unchanged


# ============================================================
# 24h forecast chart
# ============================================================


class TestSelectNext24h:
    def _cache(self, **overrides):
        base = {
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)]
            + ['2026-05-03T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [float(h) for h in range(48)],
            'hourly_prob': [h * 2 for h in range(48)],
        }
        base.update(overrides)
        return base

    def test_returns_24_hours_from_now(self):
        cache = self._cache()
        now = datetime.datetime(2026, 5, 2, 10, 30)
        result = epic._select_next_24h(cache, now)
        assert len(result) == 24
        # First hour should be 10:00 (floor of 10:30)
        assert result[0] == (10.0, 20)
        assert result[-1] == (33.0, 66)

    def test_empty_cache(self):
        assert epic._select_next_24h(None, datetime.datetime(2026, 5, 2, 12, 0)) == []
        assert epic._select_next_24h({}, datetime.datetime(2026, 5, 2, 12, 0)) == []

    def test_no_hourly_data(self):
        cache = self._cache(hourly_time=[])
        result = epic._select_next_24h(cache, datetime.datetime(2026, 5, 2, 12, 0))
        assert result == []

    def test_now_past_all_data(self):
        cache = self._cache()
        now = datetime.datetime(2026, 6, 1, 0, 0)
        assert epic._select_next_24h(cache, now) == []

    def test_window_clipped_at_end(self):
        cache = self._cache()
        # Start 6 hours before end of data — should return only 6 entries.
        now = datetime.datetime(2026, 5, 3, 18, 0)
        result = epic._select_next_24h(cache, now)
        assert len(result) == 6

    def test_handles_malformed_time(self):
        cache = self._cache(
            hourly_time=['not-a-time', '2026-05-02T13:00', '2026-05-02T14:00'],
            hourly_temp=[1.0, 2.0, 3.0],
            hourly_prob=[10, 20, 30],
        )
        now = datetime.datetime(2026, 5, 2, 12, 0)
        result = epic._select_next_24h(cache, now)
        # Skips malformed entry, finds 13:00 as first valid >= now
        assert result[0] == (2.0, 20)


class TestRenderForecastChart:
    def _screen(self):
        return pygame.Surface((480, 480))

    def _cache(self):
        return {
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [float(h) for h in range(24)],
            'hourly_prob': [h * 4 for h in range(24)],
        }

    def test_renders_with_full_data(self):
        screen = self._screen()
        screen.fill((0, 0, 0))
        now = datetime.datetime(2026, 5, 2, 0, 0)
        result = epic.render_forecast_chart(screen, self._cache(), now, 70, 30, 340, 80)
        assert result is True

    def test_skips_empty_cache(self):
        screen = self._screen()
        now = datetime.datetime(2026, 5, 2, 12, 0)
        assert epic.render_forecast_chart(screen, None, now, 70, 30, 340, 80) is False
        assert epic.render_forecast_chart(screen, {}, now, 70, 30, 340, 80) is False

    def test_skips_when_no_temps(self):
        screen = self._screen()
        cache = {
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [None] * 24,
            'hourly_prob': [10] * 24,
        }
        now = datetime.datetime(2026, 5, 2, 0, 0)
        assert epic.render_forecast_chart(screen, cache, now, 70, 30, 340, 80) is False

    def test_handles_constant_temp(self):
        # All temps equal — no division by zero.
        screen = self._screen()
        cache = {
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [5.0] * 24,
            'hourly_prob': [50] * 24,
        }
        now = datetime.datetime(2026, 5, 2, 0, 0)
        assert epic.render_forecast_chart(screen, cache, now, 70, 30, 340, 80) is True

    def test_handles_missing_probs(self):
        screen = self._screen()
        cache = {
            'hourly_time': ['2026-05-02T{:02d}:00'.format(h) for h in range(24)],
            'hourly_temp': [float(h) for h in range(24)],
            'hourly_prob': [None] * 24,
        }
        now = datetime.datetime(2026, 5, 2, 0, 0)
        assert epic.render_forecast_chart(screen, cache, now, 70, 30, 340, 80) is True


# ============================================================
# Night mode (scheduled screen on/off)
# ============================================================


class TestNightModeDefaults:
    def test_screen_on_default(self):
        assert epic.SCREEN_ON == '08:00'

    def test_screen_off_default(self):
        assert epic.SCREEN_OFF == '22:00'

    def test_night_mode_enabled_default(self):
        assert epic.NIGHT_MODE is True

    def test_backlight_gpio_default(self):
        assert epic.BACKLIGHT_GPIO == 19


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


class TestNightTransition:
    def test_on_to_off_is_sleep(self):
        assert epic.night_transition(True, False) == 'sleep'

    def test_off_to_on_is_wake(self):
        assert epic.night_transition(False, True) == 'wake'

    def test_steady_on_is_none(self):
        assert epic.night_transition(True, True) is None

    def test_steady_off_is_none(self):
        assert epic.night_transition(False, False) is None


class TestSetBacklight:
    def _no_disable_env(self):
        env = {k: v for k, v in os.environ.items() if k != 'EPIC_NO_BACKLIGHT_CTL'}
        return mock.patch.dict(os.environ, env, clear=True)

    def test_on_drives_high(self):
        with self._no_disable_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            assert epic._set_backlight(True) is True
            assert run.call_args[0][0] == ['pinctrl', 'set', str(epic.BACKLIGHT_GPIO), 'op', 'dh']

    def test_off_drives_low(self):
        with self._no_disable_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            assert epic._set_backlight(False) is True
            assert run.call_args[0][0][-1] == 'dl'

    def test_disabled_env_skips(self):
        with (
            mock.patch.dict(os.environ, {'EPIC_NO_BACKLIGHT_CTL': '1'}),
            mock.patch.object(epic.subprocess, 'run') as run,
        ):
            assert epic._set_backlight(True) is False
            run.assert_not_called()

    def test_pinctrl_missing_swallowed(self):
        with self._no_disable_env(), mock.patch.object(epic.subprocess, 'run', side_effect=FileNotFoundError):
            assert epic._set_backlight(True) is False

    def test_nonzero_exit_returns_false(self):
        with self._no_disable_env(), mock.patch.object(epic.subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=1)
            assert epic._set_backlight(True) is False

    def test_custom_gpio(self):
        with (
            self._no_disable_env(),
            mock.patch.object(epic, 'BACKLIGHT_GPIO', 12),
            mock.patch.object(epic.subprocess, 'run') as run,
        ):
            run.return_value = mock.Mock(returncode=0)
            epic._set_backlight(True)
            assert run.call_args[0][0][2] == '12'


# ============================================================
# REST API + dashboard (epic_api.py)
# ============================================================


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


class TestDispatch:
    def _provider(self):
        return lambda: {'screen_on': True, 'mode': 'photo'}

    def test_root_html(self):
        code, ctype, body, cmd = epic_api.dispatch('GET', '/', self._provider())
        assert code == 200
        assert 'text/html' in ctype
        assert b'<html' in body.lower()
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

    def test_overlay_hide(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/overlay/hide', self._provider())
        assert cmd == {'cmd': 'overlay', 'action': 'hide'}

    def test_overlay_toggle(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/overlay/toggle', self._provider())
        assert cmd == {'cmd': 'overlay', 'action': 'toggle'}

    def test_screen_on(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/screen/on', self._provider())
        assert cmd == {'cmd': 'screen', 'action': 'on'}

    def test_screen_off(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/screen/off', self._provider())
        assert cmd == {'cmd': 'screen', 'action': 'off'}

    def test_screen_auto(self):
        _, _, _, cmd = epic_api.dispatch('POST', '/api/screen/auto', self._provider())
        assert cmd == {'cmd': 'screen', 'action': 'auto'}

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
        return epic.AppState(
            mode=mode,
            current_idx=0,
            num_photos=3,
            next_photo_swap_at=now,
            next_image_api_check_at=now,
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='x',
        )

    NOW = datetime.datetime(2026, 5, 30, 12, 0)

    def test_show(self):
        s = epic.apply_overlay_command(self._state(epic.MODE_PHOTO), 'show', self.NOW)
        assert s.mode == epic.MODE_OVERLAY
        assert s.overlay_dismiss_at is None

    def test_hide(self):
        s = epic.apply_overlay_command(self._state(epic.MODE_OVERLAY), 'hide', self.NOW)
        assert s.mode == epic.MODE_PHOTO

    def test_toggle_from_photo(self):
        s = epic.apply_overlay_command(self._state(epic.MODE_PHOTO), 'toggle', self.NOW)
        assert s.mode == epic.MODE_OVERLAY
        assert s.overlay_dismiss_at is None

    def test_toggle_from_overlay(self):
        s = epic.apply_overlay_command(self._state(epic.MODE_OVERLAY), 'toggle', self.NOW)
        assert s.mode == epic.MODE_PHOTO


class TestBuildStatus:
    def _state(self):
        now = datetime.datetime(2026, 5, 30, 12, 0)
        return epic.AppState(
            mode=epic.MODE_PHOTO,
            current_idx=1,
            num_photos=4,
            next_photo_swap_at=now,
            next_image_api_check_at=now,
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data='2026-05-30 09:00:00',
        )

    def test_with_weather_is_json(self):
        weather = {
            'temp_c': 17,
            'condition': 'Rain',
            'wind_kmh': 9,
            'fetched_at': datetime.datetime(2026, 5, 30, 11, 30),
        }
        st = epic.build_status(self._state(), weather, True, 'auto', datetime.time(8, 0), datetime.time(22, 0))
        assert st['screen_on'] is True
        assert st['screen_override'] == 'auto'
        assert st['weather']['temp_c'] == 17
        assert st['weather']['fetched_at'] == '2026-05-30 11:30'
        assert st['last_image_date'] == '2026-05-30 09:00:00'
        assert st['screen_on_time'] == '08:00'
        json.dumps(st)

    def test_without_weather(self):
        st = epic.build_status(self._state(), None, False, 'off', datetime.time(8, 0), datetime.time(22, 0))
        assert st['weather'] is None
        assert st['screen_on'] is False
        assert st['screen_override'] == 'off'
        json.dumps(st)


class TestForceImageRefresh:
    def _state(self, last):
        future = datetime.datetime(2030, 1, 1)
        return epic.AppState(
            mode=epic.MODE_PHOTO,
            current_idx=0,
            num_photos=1,
            next_photo_swap_at=future,
            next_image_api_check_at=future,
            overlay_dismiss_at=None,
            blend_started_at=None,
            last_image_data=last,
        )

    @mock.patch('epic.save_photos')
    @mock.patch('epic.get_epic_images_json')
    def test_force_redownloads_same_date(self, mock_json, mock_save):
        mock_json.return_value = [{'date': '2026-05-30 09:00:00', 'image': 'img'}]
        now = datetime.datetime(2026, 5, 30, 10, 0)
        state = self._state('2026-05-30 09:00:00')
        out = epic._maybe_check_for_new_images(state, None, now, force=True)
        mock_save.assert_called_once()
        assert out.num_photos == 1

    @mock.patch('epic.save_photos')
    @mock.patch('epic.get_epic_images_json')
    def test_no_force_respects_time_gate(self, mock_json, mock_save):
        now = datetime.datetime(2026, 5, 30, 10, 0)
        state = self._state('old')
        epic._maybe_check_for_new_images(state, None, now, force=False)
        mock_json.assert_not_called()
        mock_save.assert_not_called()


class TestApiServerIntegration:
    """End-to-end over a real loopback socket — covers the handler + starter."""

    def _serve(self):
        bridge = epic_api.ApiBridge()
        bridge.set_status({'screen_on': True, 'mode': 'photo', 'weather': None})
        server = epic_api.start_api_server(bridge, '127.0.0.1', 0)
        port = server.server_address[1]
        return bridge, server, port

    def _get(self, port, path, method='GET'):
        import urllib.request

        data = b'' if method == 'POST' else None
        req = urllib.request.Request('http://127.0.0.1:' + str(port) + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_root_and_status_and_command(self):
        bridge, server, port = self._serve()
        try:
            code, body = self._get(port, '/')
            assert code == 200
            assert b'<html' in body.lower()

            code, body = self._get(port, '/api/status')
            assert code == 200
            assert json.loads(body)['mode'] == 'photo'

            code, body = self._get(port, '/api/screen/off', method='POST')
            assert code == 200
            assert bridge.drain_commands() == [{'cmd': 'screen', 'action': 'off'}]

            code, _ = self._get(port, '/nope')
            assert code == 404
        finally:
            server.shutdown()
            server.server_close()

    def test_start_returns_none_on_bad_port(self):
        bridge = epic_api.ApiBridge()
        assert epic_api.start_api_server(bridge, '127.0.0.1', -1) is None


class TestPackRgb565:
    def test_black_stays_zero(self):
        np = pytest.importorskip('numpy')
        arr = np.zeros((4, 4, 3), dtype=np.uint8)
        out = epic.pack_rgb565(arr)
        assert out.shape == (4, 4)
        assert out.dtype == np.uint16
        assert (out == 0).all()

    def test_white_is_max(self):
        np = pytest.importorskip('numpy')
        arr = np.full((4, 4, 3), 255, dtype=np.uint8)
        assert (epic.pack_rgb565(arr) == 0xFFFF).all()

    def test_pure_red_fills_top_bits_only(self):
        np = pytest.importorskip('numpy')
        arr = np.zeros((4, 4, 3), dtype=np.uint8)
        arr[:, :, 0] = 255
        out = epic.pack_rgb565(arr)
        assert ((out >> 11) == 0x1F).all()  # red maxed
        assert ((out & 0x07FF) == 0).all()  # green + blue zero

    def test_dithering_varies_spatially(self):
        np = pytest.importorskip('numpy')
        # a grey sitting between 5-bit levels must dither to more than one value
        out = epic.pack_rgb565(np.full((8, 8, 3), 12, dtype=np.uint8))
        assert len(np.unique(out)) > 1

    def test_non_multiple_of_four_size(self):
        np = pytest.importorskip('numpy')
        out = epic.pack_rgb565(np.full((7, 5, 3), 100, dtype=np.uint8))
        assert out.shape == (7, 5)
        assert out.dtype == np.uint16

    def test_channels_dither_independently(self):
        np = pytest.importorskip('numpy')
        # equal R and B input but decorrelated dither patterns -> their packed
        # 5-bit values must differ somewhere (not a shared chromatic pattern)
        out = epic.pack_rgb565(np.full((8, 8, 3), 20, dtype=np.uint8))
        r = (out >> 11) & 0x1F
        b = out & 0x1F
        assert not (r == b).all()
