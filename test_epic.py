import datetime
import io
import json
import sys
import time
from unittest import mock
from unittest.mock import MagicMock, Mock, call, patch

import pytest


# pygame must be mockable at import time since epic.py imports it at module level.
# We mock it before importing epic so no real display is needed.
mock_pygame = MagicMock()
sys.modules['pygame'] = mock_pygame

import epic


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_API_RESPONSE = [
    {
        "image": "epic_1b_20230101003634",
        "date": "2023-01-01 00:36:34",
    },
    {
        "image": "epic_1b_20230101021415",
        "date": "2023-01-01 02:14:15",
    },
    {
        "image": "epic_1b_20230101035200",
        "date": "2023-01-01 03:52:00",
    },
]


@pytest.fixture(autouse=True)
def _reset_screen():
    """Ensure epic.screen is a fresh mock for every test."""
    epic.screen = MagicMock()
    yield


# ---------------------------------------------------------------------------
# Tests for create_image_urls
# ---------------------------------------------------------------------------

class TestCreateImageUrls:
    def test_returns_correct_number_of_urls(self):
        urls = epic.create_image_urls(SAMPLE_API_RESPONSE)
        assert len(urls) == 3

    def test_url_format(self):
        urls = epic.create_image_urls(SAMPLE_API_RESPONSE)
        expected = (
            "https://epic.gsfc.nasa.gov/archive/natural/2023/01/01/jpg/"
            "epic_1b_20230101003634.jpg"
        )
        assert urls[0] == expected

    def test_all_urls_contain_base(self):
        urls = epic.create_image_urls(SAMPLE_API_RESPONSE)
        for url in urls:
            assert url.startswith("https://epic.gsfc.nasa.gov/archive/natural/")
            assert url.endswith(".jpg")

    def test_date_with_different_months(self):
        photos = [
            {"image": "epic_1b_20231215120000", "date": "2023-12-15 12:00:00"},
        ]
        urls = epic.create_image_urls(photos)
        assert "/2023/12/15/jpg/" in urls[0]

    def test_zero_pads_single_digit_month_and_day(self):
        photos = [
            {"image": "img_test", "date": "2023-03-05 08:30:00"},
        ]
        urls = epic.create_image_urls(photos)
        assert "/2023/03/05/jpg/" in urls[0]

    def test_empty_list(self):
        urls = epic.create_image_urls([])
        assert urls == []

    def test_single_photo(self):
        photos = [SAMPLE_API_RESPONSE[0]]
        urls = epic.create_image_urls(photos)
        assert len(urls) == 1

    def test_image_name_preserved(self):
        photos = [
            {"image": "my_custom_name", "date": "2023-06-15 10:00:00"},
        ]
        urls = epic.create_image_urls(photos)
        assert "my_custom_name.jpg" in urls[0]


# ---------------------------------------------------------------------------
# Tests for get_epic_images_json
# ---------------------------------------------------------------------------

class TestGetEpicImagesJson:
    @patch('epic.requests')
    def test_calls_correct_api_endpoint(self, mock_requests):
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_API_RESPONSE
        mock_requests.get.return_value = mock_response

        result = epic.get_epic_images_json()

        mock_requests.get.assert_called_once_with("https://epic.gsfc.nasa.gov/api/natural")

    @patch('epic.requests')
    def test_returns_json_response(self, mock_requests):
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_API_RESPONSE
        mock_requests.get.return_value = mock_response

        result = epic.get_epic_images_json()

        assert result == SAMPLE_API_RESPONSE
        assert len(result) == 3

    @patch('epic.requests')
    def test_returns_empty_list_from_api(self, mock_requests):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_requests.get.return_value = mock_response

        result = epic.get_epic_images_json()

        assert result == []


# ---------------------------------------------------------------------------
# Tests for save_photos
# ---------------------------------------------------------------------------

class TestSavePhotos:
    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_saves_correct_number_of_files(self, mock_urlopen, mock_pg):
        # Set up mock for urlopen to return fake image bytes
        mock_read = MagicMock()
        mock_read.read.return_value = b'fake_image_data'
        mock_urlopen.return_value = mock_read

        mock_surface = MagicMock()
        mock_pg.image.load.return_value = mock_surface
        mock_pg.Surface.return_value = mock_surface
        mock_pg.transform.scale.return_value = mock_surface

        urls = ["http://example.com/1.jpg", "http://example.com/2.jpg"]
        epic.save_photos(urls)

        assert mock_pg.image.save.call_count == 2

    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_saves_with_sequential_filenames(self, mock_urlopen, mock_pg):
        mock_read = MagicMock()
        mock_read.read.return_value = b'fake_image_data'
        mock_urlopen.return_value = mock_read

        mock_surface = MagicMock()
        mock_pg.image.load.return_value = mock_surface
        mock_pg.Surface.return_value = mock_surface
        mock_pg.transform.scale.return_value = mock_surface

        urls = ["http://example.com/a.jpg", "http://example.com/b.jpg"]
        epic.save_photos(urls)

        save_calls = mock_pg.image.save.call_args_list
        assert save_calls[0][0][1] == "./0.jpg"
        assert save_calls[1][0][1] == "./1.jpg"

    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_crops_to_830_square(self, mock_urlopen, mock_pg):
        mock_read = MagicMock()
        mock_read.read.return_value = b'fake_image_data'
        mock_urlopen.return_value = mock_read

        mock_surface = MagicMock()
        mock_pg.image.load.return_value = mock_surface
        mock_pg.Surface.return_value = mock_surface
        mock_pg.transform.scale.return_value = mock_surface

        epic.save_photos(["http://example.com/1.jpg"])

        # Verify Surface created with 830x830
        mock_pg.Surface.assert_called_with((830, 830))

    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_scales_to_480(self, mock_urlopen, mock_pg):
        mock_read = MagicMock()
        mock_read.read.return_value = b'fake_image_data'
        mock_urlopen.return_value = mock_read

        mock_surface = MagicMock()
        mock_pg.image.load.return_value = mock_surface
        mock_pg.Surface.return_value = mock_surface
        mock_pg.transform.scale.return_value = mock_surface

        epic.save_photos(["http://example.com/1.jpg"])

        mock_pg.transform.scale.assert_called_once_with(mock_surface, (480, 480))

    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_empty_url_list(self, mock_urlopen, mock_pg):
        epic.save_photos([])
        mock_pg.image.save.assert_not_called()

    @patch('epic.pygame')
    @patch('epic.urlopen')
    def test_blit_crop_region(self, mock_urlopen, mock_pg):
        mock_read = MagicMock()
        mock_read.read.return_value = b'fake_image_data'
        mock_urlopen.return_value = mock_read

        mock_loaded_image = MagicMock(name='loaded_image')
        mock_pg.image.load.return_value = mock_loaded_image

        mock_crop_surface = MagicMock(name='crop_surface')
        mock_pg.Surface.return_value = mock_crop_surface
        mock_pg.transform.scale.return_value = mock_crop_surface

        epic.save_photos(["http://example.com/1.jpg"])

        # Verify the blit uses offset (125, 125) and size (830, 830)
        mock_crop_surface.blit.assert_called_once_with(
            mock_loaded_image, (0, 0), (125, 125, 830, 830)
        )


# ---------------------------------------------------------------------------
# Tests for blend_between_photos
# ---------------------------------------------------------------------------

class TestBlendBetweenPhotos:
    @patch('epic.time')
    @patch('epic.pygame')
    def test_sets_alpha_from_0_to_255(self, mock_pg, mock_time):
        old_image = MagicMock()
        new_image = MagicMock()

        epic.blend_between_photos(old_image, new_image, 5)

        # set_alpha should be called starting at 0 then 1..255 = 256 calls total
        alpha_calls = new_image.set_alpha.call_args_list
        assert alpha_calls[0] == call(0)
        assert alpha_calls[-1] == call(255)
        assert len(alpha_calls) == 256

    @patch('epic.time')
    @patch('epic.pygame')
    def test_blits_old_then_new(self, mock_pg, mock_time):
        old_image = MagicMock()
        new_image = MagicMock()
        mock_screen = MagicMock()
        epic.screen = mock_screen

        epic.blend_between_photos(old_image, new_image, 1)

        # Verify screen.blit was called with old_image before new_image
        blit_calls = mock_screen.blit.call_args_list
        # First two calls: old at (0,0), new at (0,0)
        assert blit_calls[0] == call(old_image, (0, 0))
        assert blit_calls[1] == call(new_image, (0, 0))

    @patch('epic.time')
    @patch('epic.pygame')
    def test_sleep_duration_per_step(self, mock_pg, mock_time):
        old_image = MagicMock()
        new_image = MagicMock()

        target_duration = 5
        epic.blend_between_photos(old_image, new_image, target_duration)

        # Each sleep call should be target_duration / 255
        expected_sleep = target_duration / 255
        for c in mock_time.sleep.call_args_list:
            assert c == call(expected_sleep)

    @patch('epic.time')
    @patch('epic.pygame')
    def test_display_flip_called(self, mock_pg, mock_time):
        old_image = MagicMock()
        new_image = MagicMock()

        epic.blend_between_photos(old_image, new_image, 1)

        # flip called once initially + 255 times in the loop = 256
        assert mock_pg.display.flip.call_count == 256


# ---------------------------------------------------------------------------
# Tests for rotate_photos
# ---------------------------------------------------------------------------

class TestRotatePhotos:
    @patch('epic.time')
    @patch('epic.pygame')
    def test_loads_correct_number_of_images(self, mock_pg, mock_time):
        mock_pg.event.get.return_value = []
        mock_pg.image.load.return_value = MagicMock()

        epic.rotate_photos(3, 1)

        # Should load 0.jpg, 1.jpg, 2.jpg
        load_calls = mock_pg.image.load.call_args_list
        assert len(load_calls) == 3
        assert load_calls[0] == call(r"./0.jpg")
        assert load_calls[1] == call(r"./1.jpg")
        assert load_calls[2] == call(r"./2.jpg")

    @patch('epic.time')
    @patch('epic.pygame')
    def test_sleeps_for_rotate_delay(self, mock_pg, mock_time):
        mock_pg.event.get.return_value = []
        mock_pg.image.load.return_value = MagicMock()

        epic.rotate_photos(2, 10)

        assert mock_time.sleep.call_count == 2
        for c in mock_time.sleep.call_args_list:
            assert c == call(10)

    @patch('epic.time')
    @patch('epic.pygame')
    def test_zero_photos_does_nothing(self, mock_pg, mock_time):
        epic.rotate_photos(0, 1)
        mock_pg.image.load.assert_not_called()

    @patch('epic.blend_between_photos')
    @patch('epic.time')
    @patch('epic.pygame')
    def test_blending_enabled_calls_blend(self, mock_pg, mock_time, mock_blend):
        mock_pg.event.get.return_value = []
        mock_pg.image.load.return_value = MagicMock()

        # Blending only kicks in for counter > 1, so need at least 3 photos
        epic.rotate_photos(3, 1, blend_enabled=True, blend_time=5)

        # counter > 1 means blend is called for photo index 2
        assert mock_blend.call_count == 1

    @patch('epic.blend_between_photos')
    @patch('epic.time')
    @patch('epic.pygame')
    def test_blending_disabled_no_blend_calls(self, mock_pg, mock_time, mock_blend):
        mock_pg.event.get.return_value = []
        mock_pg.image.load.return_value = MagicMock()

        epic.rotate_photos(3, 1, blend_enabled=False)

        mock_blend.assert_not_called()

    @patch('epic.time')
    @patch('epic.pygame')
    def test_displays_image_without_blend(self, mock_pg, mock_time):
        mock_pg.event.get.return_value = []
        mock_image = MagicMock()
        mock_pg.image.load.return_value = mock_image
        mock_screen = MagicMock()
        epic.screen = mock_screen

        epic.rotate_photos(1, 1, blend_enabled=False)

        mock_screen.blit.assert_called_with(mock_image, (0, 0))
        mock_pg.display.flip.assert_called()


# ---------------------------------------------------------------------------
# Tests for init_display
# ---------------------------------------------------------------------------

class TestInitDisplay:
    @patch('epic.pygame')
    def test_sets_screen(self, mock_pg):
        mock_surface = MagicMock()
        mock_pg.display.set_mode.return_value = mock_surface
        mock_pg.FULLSCREEN = 0x80000000

        epic.init_display()

        assert epic.screen == mock_surface
        mock_pg.init.assert_called_once()
        mock_pg.display.init.assert_called_once()
        mock_pg.display.set_mode.assert_called_once_with([480, 480], mock_pg.FULLSCREEN)
        mock_pg.mouse.set_visible.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# Tests for settings / module-level constants
# ---------------------------------------------------------------------------

class TestSettings:
    def test_check_delay_default(self):
        assert epic.check_delay == 120

    def test_rotate_delay_default(self):
        assert epic.rotate_delay == 20

    def test_blending_enabled_by_default(self):
        assert epic.enable_blending is True

    def test_blending_duration_default(self):
        assert epic.blending_duration == 5
