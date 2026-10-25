
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import tempfile
import shutil
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup

from keiba_http import (
    DEFAULT_UA,
    Fetcher,
    FetchError,
    BlockedError,
    ParseError,
    HORSE_URL,
)

from keiba_common import (
    nfkc,
    now_iso,
    text_of,
    to_float,
    to_int,
)

log = logging.getLogger("horse_history")

SCHEMA_VERSION = 1
CACHE_KEEP_ROWS = 100
