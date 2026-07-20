"""Shared cohort-counting math — ONE implementation for both source modes.

m8-implementation-plan.md WP4 step 4: the sub-day SRM count stream and the
``abk plan`` arrival rate are needed by BOTH cohort-source modes — copy mode
reads the persisted ``_ab_exposures`` rows (``_ExposuresMixin``), direct mode
buckets the in-memory ``ExposureSnapshot`` — so the bucketing/bisect/rate
arithmetic lives here, pure, and the two callers can never drift.

Inputs are already-normalized naive-UTC datetimes: the mixin applies
``to_naive_utc`` to warehouse rows, ``validate_and_snapshot`` to snapshot
entries — normalization stays at the boundary that owns the raw value.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import datetime

__all__ = ["arrival_rate", "bucket_timestamps", "count_stream"]


def bucket_timestamps(
    labeled_ts: Iterable[tuple[str | None, datetime | None]],
    variants: list[str],
) -> dict[str, list[datetime]]:
    """``(variant, exposure_ts)`` pairs → sorted per-variant timestamp lists.

    A variant not in ``variants`` (not declared this run) is never counted;
    a ``None`` timestamp is dropped; every declared variant is present in the
    result (possibly empty) so downstream zero-fills read as 0, never KeyError.
    """
    per_variant: dict[str, list[datetime]] = {variant: [] for variant in variants}
    for variant, ts in labeled_ts:
        timestamps = per_variant.get(variant) if variant is not None else None
        if timestamps is None or ts is None:
            continue
        timestamps.append(ts)
    for timestamps in per_variant.values():
        timestamps.sort()
    return per_variant


def count_stream(
    per_variant_ts: dict[str, list[datetime]],
    boundaries: list[datetime],
    variants: list[str],
) -> list[dict[str, int]]:
    """Cumulative per-variant unit counts as-of each look boundary.

    For each half-open cumulative window ``[start, end_ts)``, the count of
    units first exposed BEFORE ``end_ts``: ``bisect_left(sorted_ts, boundary)``
    is exactly the count with ``exposure_ts < boundary`` (the exclusive edge,
    matching the metric-load windows). ``per_variant_ts`` lists must be sorted
    ascending (:func:`bucket_timestamps` guarantees it). Returns one dict per
    boundary, aligned and ascending — the sub-day anytime-valid SRM's input
    shape (cumulative-intervals.md §6.5).
    """
    return [
        {variant: bisect.bisect_left(per_variant_ts[variant], boundary) for variant in variants}
        for boundary in boundaries
    ]


def arrival_rate(
    per_variant_ts: dict[str, list[datetime]],
    variants: list[str],
) -> tuple[dict[str, float], float] | None:
    """Observed unit-arrival rate (units/day) per variant, or ``None``.

    Per declared variant: ``count / observed-window-days`` where the window
    spans the WHOLE cohort's ``[min, max] exposure_ts`` across declared
    variants (a shared calendar window, so the per-arm rates are mutually
    consistent). ``None`` when the window is degenerate — an empty cohort, or
    all exposures at ~one instant (``max == min``, e.g. a backfilled cohort);
    the caller then SKIPS runtime rather than inventing a rate.
    """
    earliest: datetime | None = None
    latest: datetime | None = None
    for variant in variants:
        timestamps = per_variant_ts[variant]
        if not timestamps:
            continue
        # sorted ascending ⇒ the window edges are the first/last entries
        if earliest is None or timestamps[0] < earliest:
            earliest = timestamps[0]
        if latest is None or timestamps[-1] > latest:
            latest = timestamps[-1]
    if earliest is None or latest is None:
        return None
    window_days = (latest - earliest).total_seconds() / 86400.0
    if window_days <= 0.0:
        return None
    rates = {variant: len(per_variant_ts[variant]) / window_days for variant in variants}
    return rates, window_days
