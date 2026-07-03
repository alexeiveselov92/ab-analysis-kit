"""Randomness policy for the statistical core.

Legacy hygiene fixes H1/H2 (docs/specs/statistics-changes.md §2):

- Never mutate global numpy state (``np.random.seed``): every consumer receives an
  explicit ``np.random.Generator``, so the core is reentrant and process-safe.
- Bootstrap re-runs must be byte-stable: the pipeline derives a deterministic
  per-row seed from the row identity ``(experiment, metric, name_1, name_2,
  end_date, n_samples)`` via :func:`derive_seed`. The seed is *excluded* from
  ``method_config_id`` (docs/specs/declarative-config.md §7).
"""

from __future__ import annotations

import hashlib

import numpy as np

#: Separator between seed parts (ASCII unit separator — cannot appear in dates,
#: experiment/metric/variant names), so ("ab", "c") and ("a", "bc") differ.
_PART_SEPARATOR = "\x1f"


def derive_seed(*parts: object) -> int:
    """Derive a deterministic 63-bit seed from identity parts.

    Parts are joined by their ``str()`` form — pass plain values (strings, dates,
    ints), e.g. ``derive_seed(experiment, metric, name_1, name_2, end_date,
    n_samples)``. The mapping is pinned by a known-answer test and must never
    change (it would silently re-draw every published bootstrap CI).
    """
    payload = _PART_SEPARATOR.join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1  # 63 bits, non-negative


def make_rng(seed: int | None = None) -> np.random.Generator:
    """Return a fresh, local ``Generator`` (never the global numpy RNG)."""
    return np.random.default_rng(seed)
