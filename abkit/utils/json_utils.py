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
