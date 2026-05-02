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
# blend_between_photos
# ============================================================


class TestBlendBetweenPhotos:
    def test_no_crash_without_screen(self):
        """blend_between_photos should return early when screen is None."""
        old = pygame.Surface((480, 480))
        new = pygame.Surface((480, 480))
        # Should not raise
        epic.blend_between_photos(old, new, 0.01, screen=None)

    def test_blend_with_screen(self):
        """blend_between_photos should run when given a real surface."""
        screen = pygame.Surface((480, 480))
        old = pygame.Surface((480, 480))
        new = pygame.Surface((480, 480))
        with mock.patch("epic.pygame.display.flip"):
            epic.blend_between_photos(old, new, 0.001, screen=screen)

    def test_alpha_reaches_full(self):
        """After blending, new_image alpha should be 255."""
        screen = pygame.Surface((480, 480))
        old = pygame.Surface((480, 480))
        new = pygame.Surface((480, 480))
        with mock.patch("epic.pygame.display.flip"):
            epic.blend_between_photos(old, new, 0.001, screen=screen)
        # After blend loop, transparency should have reached 255
        # (set_alpha was called with 255 as the last value)
        assert new.get_alpha() == 255


# ============================================================
# rotate_photos
# ============================================================


class TestRotatePhotos:
    def test_zero_photos(self):
        """rotate_photos with 0 photos should return immediately."""
        # Should not raise or hang
        epic.rotate_photos(0, 0.001)

    def test_rotation_loads_files(self, tmp_path):
        """rotate_photos loads numbered jpg files."""
        # Create fake image files
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            for i in range(3):
                surf = pygame.Surface((480, 480))
                pygame.image.save(surf, f"./{i}.jpg")
            with mock.patch("epic.time.sleep"):
                with mock.patch("epic.pygame.event.get", return_value=[]):
                    epic.rotate_photos(3, 0.001, screen=None)
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
    def test_init_display_calls_pygame(self, mock_visible, mock_init, mock_dinit, mock_setmode):
        fake_screen = mock.Mock()
        mock_setmode.return_value = fake_screen
        result = epic.init_display()
        mock_init.assert_called_once()
        mock_setmode.assert_called_once()
        mock_visible.assert_called_once_with(0)
        assert result == fake_screen

    @mock.patch("epic.pygame.display.set_mode")
    @mock.patch("epic.pygame.display.init")
    @mock.patch("epic.pygame.init")
    @mock.patch("epic.pygame.mouse.set_visible")
    def test_screen_filled_black(self, mock_visible, mock_init, mock_dinit, mock_setmode):
        fake_screen = mock.Mock()
        mock_setmode.return_value = fake_screen
        epic.init_display()
        fake_screen.fill.assert_called_once_with((0, 0, 0))


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
        assert hasattr(epic, "blend_between_photos")
        assert hasattr(epic, "rotate_photos")
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
# rotate_photos — additional edge cases
# ============================================================


class TestRotatePhotosAdvanced:
    def test_rotation_with_screen_displays_images(self, tmp_path):
        """rotate_photos with screen should blit images."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            for i in range(2):
                surf = pygame.Surface((480, 480))
                pygame.image.save(surf, f"./{i}.jpg")
            screen = pygame.Surface((480, 480))
            with mock.patch("epic.time.sleep"):
                with mock.patch("epic.pygame.event.get", return_value=[]):
                    with mock.patch("epic.pygame.display.flip"):
                        epic.rotate_photos(2, 0.001, screen=screen)
        finally:
            os.chdir(old_cwd)

    def test_rotation_with_blending(self, tmp_path):
        """rotate_photos with blending enabled should call blend_between_photos."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            for i in range(3):
                surf = pygame.Surface((480, 480))
                pygame.image.save(surf, f"./{i}.jpg")
            screen = pygame.Surface((480, 480))
            with mock.patch("epic.time.sleep"):
                with mock.patch("epic.pygame.event.get", return_value=[]):
                    with mock.patch("epic.pygame.display.flip"):
                        epic.rotate_photos(3, 0.001, blend_enabled=True, blend_time=0.001, screen=screen)
        finally:
            os.chdir(old_cwd)

    def test_rotation_quit_event(self, tmp_path):
        """rotate_photos should handle QUIT event."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            surf = pygame.Surface((480, 480))
            pygame.image.save(surf, "./0.jpg")
            quit_event = pygame.event.Event(pygame.QUIT)
            with mock.patch("epic.time.sleep"):
                with mock.patch("epic.pygame.event.get", return_value=[quit_event]):
                    with mock.patch("epic.pygame.quit"):
                        epic.rotate_photos(1, 0.001, screen=None)
        finally:
            os.chdir(old_cwd)

    def test_rotation_first_image_no_blend(self, tmp_path):
        """First image in rotation should not blend (counter == 0)."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            surf = pygame.Surface((480, 480))
            pygame.image.save(surf, "./0.jpg")
            screen = pygame.Surface((480, 480))
            with mock.patch("epic.time.sleep"):
                with mock.patch("epic.pygame.event.get", return_value=[]):
                    with mock.patch("epic.pygame.display.flip"):
                        with mock.patch("epic.blend_between_photos") as mock_blend:
                            epic.rotate_photos(1, 0.001, blend_enabled=True, screen=screen)
                            mock_blend.assert_not_called()
        finally:
            os.chdir(old_cwd)


# ============================================================
# main() — integration tests with mocking
# ============================================================


class TestMain:
    @mock.patch("epic.pygame.quit")
    @mock.patch("epic.rotate_photos")
    @mock.patch("epic.save_photos")
    @mock.patch("epic.create_image_urls")
    @mock.patch("epic.get_epic_images_json")
    @mock.patch("epic.init_display")
    @mock.patch("epic.pygame.display.flip")
    @mock.patch("epic.pygame.image.load")
    @mock.patch("epic.pygame.event.get")
    def test_main_loop_new_images(
        self,
        mock_events,
        mock_load,
        mock_flip,
        mock_init,
        mock_json,
        mock_urls,
        mock_save,
        mock_rotate,
        mock_quit,
    ):
        """main() should download and display new images on first run."""
        screen = pygame.Surface((480, 480))
        mock_init.return_value = screen
        mock_load.return_value = pygame.Surface((480, 480))

        # First call: return events (empty), second: return QUIT
        call_count = [0]

        def side_effect_events():
            call_count[0] += 1
            if call_count[0] >= 3:
                return [pygame.event.Event(pygame.QUIT)]
            return []

        mock_events.side_effect = side_effect_events

        mock_json.return_value = [{"date": "2025-01-01 12:00:00", "image": "test"}]
        mock_urls.return_value = ["http://example.com/test.jpg"]
        mock_rotate.return_value = None

        # Override check_delay to prevent long waits
        old_delay = epic.check_delay
        epic.check_delay = 0
        try:
            epic.main()
        except SystemExit:
            pass
        finally:
            epic.check_delay = old_delay

        mock_json.assert_called()
        mock_urls.assert_called()
        mock_save.assert_called()

    @mock.patch("epic.pygame.quit")
    @mock.patch("epic.rotate_photos")
    @mock.patch("epic.save_photos")
    @mock.patch("epic.create_image_urls")
    @mock.patch("epic.get_epic_images_json")
    @mock.patch("epic.init_display")
    @mock.patch("epic.pygame.display.flip")
    @mock.patch("epic.pygame.image.load")
    @mock.patch("epic.pygame.event.get")
    def test_main_no_new_images(
        self,
        mock_events,
        mock_load,
        mock_flip,
        mock_init,
        mock_json,
        mock_urls,
        mock_save,
        mock_rotate,
        mock_quit,
    ):
        """main() should skip download when data hasn't changed."""
        screen = pygame.Surface((480, 480))
        mock_init.return_value = screen
        mock_load.return_value = pygame.Surface((480, 480))

        call_count = [0]
        json_call_count = [0]

        def side_effect_events():
            call_count[0] += 1
            if call_count[0] >= 4:
                return [pygame.event.Event(pygame.QUIT)]
            return []

        mock_events.side_effect = side_effect_events

        # Return same data each time — second time should be "no new images"
        mock_json.return_value = [{"date": "2025-01-01 12:00:00", "image": "test"}]
        mock_urls.return_value = ["http://example.com/test.jpg"]
        mock_rotate.return_value = None

        old_delay = epic.check_delay
        epic.check_delay = 0
        try:
            epic.main()
        except SystemExit:
            pass
        finally:
            epic.check_delay = old_delay

        # save_photos should be called only once (first run), not on repeat
        assert mock_save.call_count == 1


# ============================================================
# blend_between_photos — edge cases
# ============================================================


class TestBlendEdgeCases:
    def test_blend_zero_duration(self):
        """Blend with very short duration should still complete."""
        screen = pygame.Surface((480, 480))
        old = pygame.Surface((480, 480))
        new = pygame.Surface((480, 480))
        with mock.patch("epic.pygame.display.flip"):
            epic.blend_between_photos(old, new, 0.0001, screen=screen)

    def test_blend_surfaces_are_correct_size(self):
        """Blend should work with DISPLAY_SIZE surfaces."""
        screen = pygame.Surface(epic.DISPLAY_SIZE)
        old = pygame.Surface(epic.DISPLAY_SIZE)
        new = pygame.Surface(epic.DISPLAY_SIZE)
        with mock.patch("epic.pygame.display.flip"):
            epic.blend_between_photos(old, new, 0.001, screen=screen)


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
        assert cache['sunrise'] == '06:42'
        assert cache['sunset'] == '19:08'
        assert cache['rain_today'] == (60, 2.0)
        assert cache['rain_tomorrow'] == (80, 5.4)
        assert isinstance(cache['fetched_at'], datetime.datetime)
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
