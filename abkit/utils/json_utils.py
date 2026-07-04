"""Canonical JSON serialisation.

This is the ONE serialisation path used both for ``method_config_id`` hashing and
for persisting ``method_params`` (docs/specs/declarative-config.md §7,
docs/specs/quorum-review.md "canonical method_params JSON everywhere"). Exact-string
BI filters and identity hashes must never disagree, so nothing else may serialise
method params.
"""

from __future__ import annotations

import json
from typing import Any


def json_dumps_sorted(obj: Any) -> str:
    """Serialise ``obj`` to canonical JSON: sorted keys, no whitespace, UTF-8 text.

    ``allow_nan=False`` — NaN/Infinity are not valid JSON and must never appear in
    method params (they would silently break BI series identity).
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def json_loads(value: str | bytes) -> Any:
    """Parse a JSON document (stdlib only — this module must stay import-light).

    Accepts ``bytes``/``bytearray``/``memoryview`` and str subclasses such as
    ``numpy.str_`` (coerced to exact ``str`` first, matching the dumps path's
    text-in/text-out convention).
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    elif type(value) is not str:
        value = str(value)
    return json.loads(value)
