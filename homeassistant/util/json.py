"""JSON utility functions."""
import logging
from typing import Union, List, Dict

import os
import six
import json
import tempfile
import io
import codecs
import functools

_LOGGER = logging.getLogger(__name__)


def load_json(filename: str, **kwargs) -> Union[List, Dict]:
    """Load JSON data from a file and return as dict or list.

    Defaults to returning empty dict if file is not found.
    """
    try:
        with open(filename, 'r') as fh:
            data = json.load(fh, **kwargs)
    # This is not a fatal error
    except FileNotFoundError:
        _LOGGER.debug('File not found: %r; Pretending it just contained an empty mapping I guess.' % filename)
        data = {}

    return data


def save_json(filename: str, data: Union[List, Dict], sort_keys: bool=True, indent: int=4, **kwargs) -> int:
    """Save JSON data to a file.

    Just like `write()` returns the number of bytes written.
    """
    json_data = json.dumps(data, sort_keys=sort_keys, indent=indent, **kwargs)

    with open(filename, 'w+') as fh:
        fh.seek(0)
        cnt = fh.write(json_data)
        fh.truncate()

    return cnt

