"""Domain-neutral helpers shared across abkit (stdlib-only at import time).

This package is imported by ``abkit.stats`` (the pure core), so nothing here
may import numpy/pydantic/yaml/click/jinja2/orjson — enforced by
``tests/stats/test_purity.py``.
"""

from abkit.utils.datetime_utils import (
    format_duration,
    now_utc,
    now_utc_naive,
    to_aware_utc,
    to_naive_utc,
)
from abkit.utils.env_interpolation import interpolate_env_vars
from abkit.utils.json_utils import json_dumps_sorted, json_loads

__all__ = [
    "format_duration",
    "interpolate_env_vars",
    "json_dumps_sorted",
    "json_loads",
    "now_utc",
    "now_utc_naive",
    "to_aware_utc",
    "to_naive_utc",
]
