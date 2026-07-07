#!/usr/bin/env python3
"""gen-demo-golden.py — golden-parity vectors for the landing stabilization demo.

The interactive landing demo re-implements abkit's cumulative-effect computation
in TypeScript (``website/src/scripts/demo/stats.ts``): it folds per-day
sufficient statistics with abkit's Chan/Welford merge and reduces them to an
absolute two-sample effect + Normal confidence interval per day. To prove that
port stays faithful, we do NOT eyeball it: this script runs the *real*
``abkit.stats`` primitives over a handful of fixed, seeded synthetic experiments
and freezes their per-day output to ``website/src/scripts/demo/golden.json``.
``check-demo-parity.mjs`` then bundles ``stats.ts`` and asserts the TS
``runCumulative`` reproduces every frozen point (effect / CI / p-value / reject)
within 1e-6.

Run it from the repo with the project venv (statsmodels/scipy must import):

    .venv/bin/python website/scripts/gen-demo-golden.py

The output is fully deterministic — fixed ``numpy.random.RandomState`` seeds,
integer daily unit counts, and sorted JSON keys — so re-running only changes the
file when the abkit math actually changes, keeping git diffs meaningful.

WHAT IS FROZEN, and why it is faithful (never hand-edit these numbers):

  * Each case's ``days`` block carries, per day per arm, the abkit
    ``SufficientStats`` triple (n, mean, m2) built by ``SufficientStats.from_sample``
    over that day's seeded draws. This is the port's INPUT (the honest boundary,
    analogous to the donor freezing raw series values).
  * The cumulative effect + CI is computed exactly as the product does: fold the
    daily suffstats with ``merge_suffstats`` (abkit/stats/accumulate.py — the
    incremental primitive; cumulative-intervals.md §3 notes every quantity the
    t-test needs derives from the six suffstat numbers, and the v1/v2 compute
    paths agree to floating-point round-off), then run the ``t-test`` method with
    ``test_type="absolute"`` at alpha=0.05. The ``expected`` block is that
    ``TestResult`` (effect, left/right bound, p-value, reject), per day.

The TS side folds the SAME frozen daily suffstats with the SAME Chan formula and
the SAME normal math, so the CI bounds match to the ULP; the p-value matches to
~1e-7 (the port's erfc) — both well inside the 1e-6 gate.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from abkit.stats import Sample, SufficientStats, create_method, merge_suffstats

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT = _REPO_ROOT / "website" / "src" / "scripts" / "demo" / "golden.json"

_ALPHA = 0.05


def _clean(x: float | None) -> float | None:
    """Render a float for JSON; NaN/inf/None → null (matches TestResult.to_dict)."""
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return float(x)


# ---------------------------------------------------------------------------
# Case specifications
# ---------------------------------------------------------------------------
#
# Each case fixes a seeded synthetic experiment. ``units_ctrl``/``units_trt`` are
# the per-day new-unit counts per arm (an int, or a callable of the 1-based day
# for schedules like a single-unit warm-up). The cases span the math paths the
# port must reproduce: a converging positive effect, a converging negative
# effect, an exact null, unequal arm traffic, a high-variance slow-converger, and
# a degenerate day-1 (single unit per arm → zero variance → un-scored, no CI).


def _spec(
    name: str,
    *,
    seed: int,
    days: int,
    horizon: int,
    base_mean: float,
    true_effect: float,
    sigma: float,
    units_ctrl: int | Callable[[int], int],
    units_trt: int | Callable[[int], int],
) -> dict[str, Any]:
    return {
        "name": name,
        "seed": seed,
        "days": days,
        "horizon": horizon,
        "base_mean": base_mean,
        "true_effect": true_effect,
        "sigma": sigma,
        "units_ctrl": units_ctrl,
        "units_trt": units_trt,
    }


def _make_specs() -> list[dict[str, Any]]:
    return [
        _spec(
            "converging_win",
            seed=101, days=28, horizon=14,
            base_mean=0.50, true_effect=0.020, sigma=0.20,
            units_ctrl=45, units_trt=45,
        ),
        _spec(
            "converging_lose",
            seed=202, days=28, horizon=14,
            base_mean=0.50, true_effect=-0.020, sigma=0.20,
            units_ctrl=45, units_trt=45,
        ),
        _spec(
            "flat_null",
            seed=303, days=28, horizon=14,
            base_mean=1.00, true_effect=0.0, sigma=0.25,
            units_ctrl=60, units_trt=60,
        ),
        _spec(
            "unequal_traffic",
            seed=404, days=21, horizon=10,
            base_mean=2.00, true_effect=0.06, sigma=0.40,
            units_ctrl=70, units_trt=30,
        ),
        _spec(
            "high_variance_slow",
            seed=505, days=28, horizon=14,
            base_mean=0.80, true_effect=0.010, sigma=0.35,
            units_ctrl=40, units_trt=40,
        ),
        _spec(
            "warmup_degenerate",
            seed=606, days=16, horizon=8,
            base_mean=0.50, true_effect=0.030, sigma=0.20,
            units_ctrl=lambda d: 1 if d == 1 else 50,
            units_trt=lambda d: 1 if d == 1 else 50,
        ),
    ]


def _units(spec_value: int | Callable[[int], int], day: int) -> int:
    return spec_value(day) if callable(spec_value) else spec_value


# ---------------------------------------------------------------------------
# Run one case through the real abkit.stats math
# ---------------------------------------------------------------------------


def _run_case(spec: dict[str, Any]) -> dict[str, Any]:
    rng = np.random.RandomState(spec["seed"])
    method = create_method("t-test", alpha=_ALPHA, params={"test_type": "absolute"})

    days_block: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    cum_c: SufficientStats | None = None
    cum_t: SufficientStats | None = None

    for day in range(1, spec["days"] + 1):
        n_c = _units(spec["units_ctrl"], day)
        n_t = _units(spec["units_trt"], day)
        c_vals = spec["base_mean"] + rng.normal(0.0, spec["sigma"], n_c)
        t_vals = spec["base_mean"] + spec["true_effect"] + rng.normal(0.0, spec["sigma"], n_t)

        c_suff = SufficientStats.from_sample(Sample(c_vals, name="control"))
        t_suff = SufficientStats.from_sample(Sample(t_vals, name="treatment"))

        cum_c = c_suff if cum_c is None else merge_suffstats(cum_c, c_suff)
        cum_t = t_suff if cum_t is None else merge_suffstats(cum_t, t_suff)

        result = method.from_suffstats(cum_c, cum_t)
        scored = math.isfinite(result.left_bound) and math.isfinite(result.right_bound)

        days_block.append(
            {
                "ed": day,
                "control": {"n": c_suff.n, "mean": c_suff.mean, "m2": c_suff.m2},
                "treatment": {"n": t_suff.n, "mean": t_suff.mean, "m2": t_suff.m2},
            }
        )
        expected.append(
            {
                "ed": day,
                "n1": result.size_1,
                "n2": result.size_2,
                "effect": _clean(result.effect),
                "lo": _clean(result.left_bound) if scored else None,
                "hi": _clean(result.right_bound) if scored else None,
                "p": _clean(result.pvalue) if scored else None,
                "reject": bool(result.reject),
                "scored": bool(scored),
            }
        )

    return {
        "name": spec["name"],
        "config": {"alpha": _ALPHA, "horizonDay": spec["horizon"]},
        "days": days_block,
        "expected": expected,
    }


def main() -> None:
    cases = [_run_case(spec) for spec in _make_specs()]
    doc = {"cases": cases}
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, sort_keys=True, allow_nan=False)
    _OUT.write_text(text + "\n", encoding="utf-8")

    n_points = sum(len(c["expected"]) for c in cases)
    print(
        f"gen-demo-golden: wrote {_OUT.relative_to(_REPO_ROOT)} "
        f"({len(cases)} cases, {n_points} points, {len(text)} bytes)"
    )


if __name__ == "__main__":
    main()
