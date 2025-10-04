# standard imports
import json
import os
import pathlib
import time
from typing import Union
from urllib3 import Retry

# lib imports
import cloudscraper
from PIL import Image
import requests
from requests.adapters import HTTPAdapter

# local imports
from src.logger import log

# constants
HTTPS = 'https://'

# setup requests sessions
retry_adapter = HTTPAdapter(max_retries=Retry(total=5, backoff_factor=1))

# cloudscraper session
cs = cloudscraper.CloudScraper()  # CloudScraper inherits from requests.Session
cs.mount(HTTPS, retry_adapter)

# requests session
s = requests.Session()
s.mount(HTTPS, retry_adapter)


class RateLimitedSession(requests.Session):
    """
    A requests.Session subclass that implements rate limiting.
    """
    def __init__(self, calls_per_minute=60):
        super().__init__()
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute  # seconds between calls
        self.last_call_time = 0

    def request(self, *args, **kwargs):
        # Calculate time since last call
        elapsed = time.time() - self.last_call_time

        # If we need to wait, sleep for the remaining time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        # Make the request
        self.last_call_time = time.time()
        return super().request(*args, **kwargs)


# readthedocs rate-limited session (60 authenticated requests per minute)
rtd_s = RateLimitedSession(calls_per_minute=60)
rtd_s.mount(HTTPS, retry_adapter)


def debug_print(
        *values: object,
        sep: Union[str, None] = ' ',
        end: Union[str, None] = '\n',
):
    log.debug(msg=sep.join(map(str, values)))
    if os.getenv('ACTIONS_RUNNER_DEBUG') or os.getenv('ACTIONS_STEP_DEBUG'):
        print(*values, sep=sep, end=end)


def save_image_from_url(file_path: str, file_extension: str, image_url: str, size_x: int = 0, size_y: int = 0):
    """
    Write image data to file. If ``size_x`` and ``size_y`` are both supplied, a resized image will also be saved.

    Parameters
    ----------
    file_path : str
        The file path to save the file at.
    file_extension : str
        The extension of the file name.
    image_url : str
        The image url.
    size_x : int
        The ``x`` dimension to resize the image to. If used, ``size_y`` must also be defined.
    size_y : int
        The ``y`` dimension to resize the image to. If used, ``size_x`` must also be defined.
    """
    debug_print(f'Saving image from {image_url}')
    # determine the directory
    directory = os.path.dirname(file_path)

    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    og_img_data = s.get(url=image_url).content

    file_name_with_ext = f'{file_path}.{file_extension}'
    with open(file_name_with_ext, 'wb') as handler:
        handler.write(og_img_data)

    # resize the image
    if size_x and size_y:
        pil_img_data = Image.open(file_name_with_ext)
        resized_img_data = pil_img_data.resize((size_x, size_y))
        resized_img_data.save(fp=f'{file_path}_{size_x}x{size_y}.{file_extension}')


def write_json_files(file_path: str, data: any):
    """
    Write dictionary to JSON file.

    Parameters
    ----------
    file_path : str
        The file path to save the file at, excluding the file extension which will be `.json`
    data
        The dictionary data to write in the JSON file.
    """
    debug_print(f'Writing json file at {file_path}')
    # determine the directory
    directory = os.path.dirname(file_path)

    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    with open(f'{file_path}.json', 'w') as f:
        json.dump(
            obj=data,
            fp=f,
            indent=4 if os.getenv('ACTIONS_RUNNER_DEBUG') or os.getenv('ACTIONS_STEP_DEBUG') else None,
        )
