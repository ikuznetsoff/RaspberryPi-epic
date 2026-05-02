import datetime
import io
import json
import time
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


def blend_between_photos(old_image, new_image, target_duration, screen=None):
    if screen is None:
        return
    print("Attempting to blend between old and new images")

    transparency = 0
    # Place the old image down first
    screen.blit(old_image, (0, 0))
    # Set the new image to be completely transparent
    new_image.set_alpha(transparency)
    screen.blit(new_image, (0, 0))
    pygame.display.flip()

    while transparency < 255:
        # Update transparency for new image
        transparency += 1
        new_image.set_alpha(transparency)

        # Place both images down, old one first, new one with adjusted transparency second.
        screen.blit(old_image, (0, 0))
        screen.blit(new_image, (0, 0))
        pygame.display.flip()
        # Delay the loop to blend over the target duration (in seconds)
        time.sleep(target_duration / 255)


def rotate_photos(num_photos, rotate_delay, blend_enabled=False, blend_time=5, screen=None):
    counter = 0
    while counter < num_photos:
        # First check if anyone's tried to quit the app while we've been rotating
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()

        # Create a surface object and draw image on it.
        new_image = pygame.image.load(r"./" + str(counter) + ".jpg")
        if counter > 0 and blend_enabled and screen is not None:
            old_image = pygame.image.load(r"./" + str(counter - 1) + ".jpg")
            blend_between_photos(old_image, new_image, blend_time, screen)
        elif screen is not None:
            # Display image
            screen.blit(new_image, (0, 0))
            pygame.display.flip()

        counter += 1

        # How many seconds to wait between changing images
        time.sleep(rotate_delay)


def init_display():
    """Initialize pygame and create display surface."""
    import os

    pygame.init()
    os.environ["DISPLAY"] = ":0"
    pygame.display.init()
    screen = pygame.display.set_mode(list(DISPLAY_SIZE), pygame.FULLSCREEN)
    pygame.mouse.set_visible(0)
    screen.fill((0, 0, 0))
    return screen


def main():
    screen = init_display()

    # Display loading image
    image = pygame.image.load(r"./loading.jpg")
    screen.blit(image, (0, 0))
    pygame.display.flip()

    print("Checking for new photos every " + str(check_delay) + " minutes")
    print("Rotating photos every " + str(rotate_delay) + " seconds")

    # Run until the user asks to quit
    running = True
    first_run = True
    last_data = ""
    newest_data = ""
    last_check = datetime.datetime.now() - datetime.timedelta(hours=1)
    num_photos = 0

    while running:
        # Did anyone try to quit the app?
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                pygame.quit()

        # If we haven't checked for new images recently, check for new images
        if last_check < datetime.datetime.now() - datetime.timedelta(minutes=check_delay) or first_run == True:
            print(str(datetime.datetime.now()) + " Checking for new images.")

            last_check = datetime.datetime.now()
            first_run = False

            image_data = get_epic_images_json()
            newest_data = image_data[0]["date"]

            print("OLD: " + last_data)
            print("NEW: " + newest_data)

            # If there are new images available, download them, then quickly display them all.
            if last_data != newest_data:
                print("Ooh! New Images!")
                last_data = newest_data
                imageurls = create_image_urls(image_data)
                save_photos(imageurls, screen)
                num_photos = len(imageurls)
                rotate_photos(num_photos, 1, screen=screen)
            else:
                print("No new images")

        # Show each photo in order.
        rotate_photos(num_photos, rotate_delay, enable_blending, blending_duration, screen=screen)

    # Done! Time to quit.
    pygame.quit()


if __name__ == "__main__":
    main()
