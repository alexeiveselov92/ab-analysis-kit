"""WP1 golden gate: the ndtri/ndtr scalar hot path vs the frozen pre-WP1 reference.

M7 WP1 (docs/specs/m7-implementation-plan.md §WP1) replaces the frozen
``sps.norm(loc, scale)`` construction on the closed-form significance path
(``effects.normal_test``, the z-test inline formula,
``sequential.se_from_ci_length``) with ``scipy.special.ndtri``/``ndtr`` — using
``ndtr(-z)``, never ``1 - ndtr(z)``, for the sf-equivalent tail — and makes
``TestResult.effect_distribution`` lazy. The milestone invariant is BYTE parity:
this test replays a battery frozen from the pre-change code (commit ``68d3fa8``,
captured before any WP1 edit) — ``tests/stats/fixtures/normal_path_golden.json``
— and asserts bit-identical outputs (floats compared by ``float.hex``),
identical warnings, and identical reject flags. The battery deliberately
includes extreme-z fixtures (the §0.3(2) review landmine: a backwards
``1 - ndtr(z)`` tail drifts silently for large ``|z|``), degenerate rows
(``var <= 0``, non-finite, zero denominators, pooled proportion 0/1), and
end-to-end method results so the A7 ``_result_from_normal_test`` assembly
refactor is pinned field-by-field.

The fixture is a one-time capture: it can only be regenerated from a checkout
predating WP1 (the capture script simply dumps ``_encode(capture())`` — see the
M7 WP1 PR). Do not loosen the equality here; a genuine formula change is
ALGORITHM_VERSION territory, not a tolerance bump.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from abkit.stats import Fraction, RatioSample, Sample, create_method
from abkit.stats.effects import EffectEstimate, normal_test
from abkit.stats.result import TestResult
from abkit.stats.sequential.confidence_sequence import se_from_ci_length

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "normal_path_golden.json"

NAN = float("nan")
INF = float("inf")

# --- the frozen battery ---------------------------------------------------------------
# (case_id, effect, var, alpha) — extreme-z rows are the §0.3(2) tail-parity fixtures.
NORMAL_TEST_CASES: tuple[tuple[str, float, float, float], ...] = (
    ("plain-small-lift", 0.03, 0.0004, 0.05),
    ("plain-negative", -0.11, 0.0025, 0.05),
    ("zero-effect", 0.0, 1.0, 0.05),
    ("near-zero-effect", 1e-15, 1.0, 0.05),
    ("tight-alpha", 1.0, 1.0, 0.001),
    ("loose-alpha", 1.0, 1.0, 0.2),
    ("micro-alpha", 1.0, 1.0, 1e-8),
    ("extreme-z8", 8.0, 1.0, 0.05),
    ("extreme-z20-negative", -20.0, 1.0, 0.05),
    ("extreme-z37-underflow-edge", 37.0, 1.0, 0.05),
    ("extreme-z40-underflow", 40.0, 1.0, 0.05),
    ("extreme-z100", 10.0, 0.01, 0.05),
    ("extreme-tiny-var", 3.0, 1e-300, 0.05),
    ("extreme-huge-scale", 1e300, 1e300, 0.05),
    ("degenerate-zero-var", 0.5, 0.0, 0.05),
    ("degenerate-negative-var", 0.5, -1.0, 0.05),
    ("degenerate-nan-var", 0.5, NAN, 0.05),
    ("degenerate-nan-effect", NAN, 1.0, 0.05),
    ("degenerate-inf-effect", INF, 1.0, 0.05),
    ("degenerate-inf-var", 1.0, INF, 0.05),
)

# (case_id, ci_length, alpha)
SE_FROM_CI_CASES: tuple[tuple[str, float, float], ...] = (
    ("plain", 0.4, 0.05),
    ("tight-alpha", 1.2, 0.001),
    ("micro-alpha", 0.7, 1e-8),
    ("zero-length", 0.0, 0.05),
    ("negative-length", -0.5, 0.05),
    ("nan-length", NAN, 0.05),
    ("inf-length", INF, 0.05),
)

# (case_id, alpha, params, (count_1, nobs_1), (count_2, nobs_2))
ZTEST_CASES: tuple[
    tuple[str, float, dict[str, Any], tuple[float, float], tuple[float, float]], ...
] = (
    ("relative-plain", 0.05, {}, (50.0, 1000.0), (65.0, 1000.0)),
    ("absolute-plain", 0.05, {"test_type": "absolute"}, (50.0, 1000.0), (65.0, 1000.0)),
    ("relative-tight-alpha", 0.001, {}, (480.0, 5000.0), (520.0, 5000.0)),
    ("extreme-z", 0.05, {}, (1.0, 1_000_000.0), (2000.0, 1_000_000.0)),
    (
        "extreme-z-absolute",
        0.001,
        {"test_type": "absolute"},
        (10_000.0, 1_000_000.0),
        (12_000.0, 1_000_000.0),
    ),
    ("degenerate-pooled-zero", 0.05, {}, (0.0, 100.0), (0.0, 100.0)),
    ("degenerate-pooled-one", 0.05, {}, (100.0, 100.0), (100.0, 100.0)),
    ("relative-zero-control", 0.05, {}, (0.0, 100.0), (5.0, 100.0)),
    ("absolute-zero-control", 0.05, {"test_type": "absolute"}, (0.0, 100.0), (5.0, 100.0)),
    ("with-mde", 0.05, {"calculate_mde": True}, (300.0, 2000.0), (345.0, 2100.0)),
)


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _ttest_pair(
    seed: int = 101, n1: int = 997, n2: int = 1013, shift: float = 0.05
) -> tuple[Sample, Sample]:
    rng = _rng(seed)
    control = Sample(rng.normal(10.0, 2.0, n1), name="control")
    treatment = Sample(rng.normal(10.0 + shift, 2.0, n2), name="treatment")
    return control, treatment


def _cuped_pair(
    seed: int = 201,
    n1: int = 800,
    n2: int = 820,
    rho: float = 0.8,
    shift: float = 0.03,
    constant_covariate: bool = False,
) -> tuple[Sample, Sample]:
    rng = _rng(seed)

    def _arm(n: int, loc_shift: float, name: str) -> Sample:
        covariate = np.full(n, 7.0) if constant_covariate else rng.normal(10.0, 2.0, n)
        noise = rng.normal(0.0, 1.2, n)
        values = 2.0 + rho * covariate + noise + loc_shift
        return Sample(values, cov_array=covariate, name=name)

    return _arm(n1, 0.0, "control"), _arm(n2, shift, "treatment")


def _paired_pair(
    seed: int = 301, n: int = 900, shift: float = 0.04, covariate: bool = False
) -> tuple[Sample, Sample]:
    rng = _rng(seed)
    base = rng.normal(10.0, 2.0, n)
    cov_1 = rng.normal(10.0, 2.0, n) if covariate else None
    cov_2 = rng.normal(10.0, 2.0, n) if covariate else None
    control = Sample(base + rng.normal(0.0, 0.5, n), cov_array=cov_1, name="control")
    treatment = Sample(base + shift + rng.normal(0.0, 0.5, n), cov_array=cov_2, name="treatment")
    return control, treatment


def _ratio_pair(
    seed: int = 401, n1: int = 700, n2: int = 730, zero_denominator: bool = False
) -> tuple[RatioSample, RatioSample]:
    rng = _rng(seed)
    den_1 = np.zeros(n1) if zero_denominator else rng.poisson(5.0, n1).astype(float) + 1.0
    den_2 = rng.poisson(5.0, n2).astype(float) + 1.0
    num_1 = rng.normal(2.0, 1.0, n1) * (den_1 if not zero_denominator else 1.0)
    num_2 = rng.normal(2.1, 1.0, n2) * den_2
    return RatioSample(num_1, den_1, name="control"), RatioSample(num_2, den_2, name="treatment")


#: End-to-end method results: pins the shared TestResult assembly (A7) field-by-field.
METHOD_CASES: tuple[tuple[str, Callable[[], TestResult]], ...] = (
    ("ttest-relative", lambda: create_method("t-test").compare_pair(*_ttest_pair())),
    (
        "ttest-absolute",
        lambda: create_method("t-test", params={"test_type": "absolute"}).compare_pair(
            *_ttest_pair()
        ),
    ),
    (
        "ttest-with-mde",
        lambda: create_method("t-test", params={"calculate_mde": True}).compare_pair(
            *_ttest_pair(seed=102)
        ),
    ),
    (
        "ttest-degenerate-constant",
        lambda: create_method("t-test").compare_pair(
            Sample(np.full(50, 3.0), name="control"), Sample(np.full(60, 3.0), name="treatment")
        ),
    ),
    ("cuped-relative", lambda: create_method("cuped-t-test").compare_pair(*_cuped_pair())),
    (
        "cuped-absolute",
        lambda: create_method("cuped-t-test", params={"test_type": "absolute"}).compare_pair(
            *_cuped_pair()
        ),
    ),
    (
        "cuped-low-correlation",
        lambda: create_method("cuped-t-test").compare_pair(*_cuped_pair(seed=202, rho=0.05)),
    ),
    (
        "cuped-degenerate-covariate",
        lambda: create_method("cuped-t-test").compare_pair(
            *_cuped_pair(seed=203, constant_covariate=True)
        ),
    ),
    (
        "cuped-with-mde",
        lambda: create_method("cuped-t-test", params={"calculate_mde": True}).compare_pair(
            *_cuped_pair(seed=204)
        ),
    ),
    ("paired-relative", lambda: create_method("paired-t-test").compare_pair(*_paired_pair())),
    (
        "paired-absolute",
        lambda: create_method("paired-t-test", params={"test_type": "absolute"}).compare_pair(
            *_paired_pair()
        ),
    ),
    (
        "paired-cuped-relative",
        lambda: create_method("paired-cuped-t-test").compare_pair(
            *_paired_pair(seed=302, covariate=True)
        ),
    ),
    (
        "paired-cuped-absolute",
        lambda: create_method("paired-cuped-t-test", params={"test_type": "absolute"}).compare_pair(
            *_paired_pair(seed=303, covariate=True)
        ),
    ),
    ("ratio-delta-relative", lambda: create_method("ratio-delta").compare_pair(*_ratio_pair())),
    (
        "ratio-delta-absolute",
        lambda: create_method("ratio-delta", params={"test_type": "absolute"}).compare_pair(
            *_ratio_pair()
        ),
    ),
    (
        "ratio-delta-zero-denominator",
        lambda: create_method("ratio-delta").compare_pair(
            *_ratio_pair(seed=402, zero_denominator=True)
        ),
    ),
)


# --- capture & byte-exact encoding ------------------------------------------------------


def _encode(value: Any) -> Any:
    """JSON-safe, byte-exact encoding: every float becomes ``{"f": float.hex()}``."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return {"f": value.hex()}
    if isinstance(value, dict):
        return {key: _encode(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(entry) for entry in value]
    raise TypeError(f"unexpected fixture value type: {type(value).__name__}")


def _distribution_probes(distribution: Any) -> dict[str, Any]:
    """Pin the effect-distribution contract: presence + delegated scipy reads.

    The WP1 lazy proxy must answer ``.cdf``/``.ppf``/``.sf`` with the exact bytes
    the eagerly-frozen ``sps.norm`` produced.
    """
    probes: dict[str, Any] = {"has_distribution": distribution is not None}
    if distribution is not None:
        probes["dist_cdf_0"] = float(distribution.cdf(0.0))
        probes["dist_ppf_975"] = float(distribution.ppf(0.975))
        probes["dist_sf_1"] = float(distribution.sf(1.0))
    return probes


def capture() -> dict[str, Any]:
    """Run the whole battery against the CURRENT code and return raw outputs."""
    out: dict[str, Any] = {"normal_test": {}, "se_from_ci_length": {}, "ztest": {}, "methods": {}}

    for case_id, effect, var, alpha in NORMAL_TEST_CASES:
        test = normal_test(EffectEstimate(effect=effect, var=var), alpha)
        record: dict[str, Any] = {
            "effect": test.effect,
            "left_bound": test.left_bound,
            "right_bound": test.right_bound,
            "ci_length": test.ci_length,
            "pvalue": test.pvalue,
            "reject": test.reject,
            "warnings": list(test.warnings),
        }
        record.update(_distribution_probes(test.distribution))
        out["normal_test"][case_id] = record

    for case_id, ci_length, alpha in SE_FROM_CI_CASES:
        out["se_from_ci_length"][case_id] = {"se": se_from_ci_length(ci_length, alpha)}

    for case_id, alpha, params, (count_1, nobs_1), (count_2, nobs_2) in ZTEST_CASES:
        method = create_method("z-test", alpha=alpha, params=params)
        result = method.compare_pair(
            Fraction(count_1, nobs_1, name="control"), Fraction(count_2, nobs_2, name="treatment")
        )
        record = result.to_dict()
        record.update(_distribution_probes(result.effect_distribution))
        out["ztest"][case_id] = record

    for case_id, build in METHOD_CASES:
        result = build()
        record = result.to_dict()
        record.update(_distribution_probes(result.effect_distribution))
        out["methods"][case_id] = record

    return out


# --- the gate ---------------------------------------------------------------------------


@pytest.mark.golden
def test_scalar_normal_path_matches_frozen_pre_wp1_reference() -> None:
    frozen = json.loads(FIXTURE_PATH.read_text())
    frozen.pop("_provenance", None)
    current = _encode(capture())

    for section, cases in frozen.items():
        for case_id, expected in cases.items():
            got = current[section][case_id]
            assert got == expected, (
                f"{section}/{case_id}: scalar-path output drifted from the frozen pre-WP1 "
                f"reference.\nexpected: {expected}\ngot:      {got}"
            )
    # Both directions: a silently *added* case section would hide a rename.
    assert {k: set(v) for k, v in frozen.items()} == {
        section: set(cases) for section, cases in current.items()
    }


def test_effect_distribution_is_lazy_and_truthy() -> None:
    """WP1 A3: the ``is not None`` contract holds; ``to_dict`` never materializes.

    White-box on purpose: ``_frozen`` is the laziness invariant this pin exists
    to protect (stats-core-review A3 requires the truthiness contract to
    survive the deferral).
    """
    result = create_method("t-test").compare_pair(*_ttest_pair(seed=505))
    distribution = result.effect_distribution
    assert distribution is not None
    assert distribution._frozen is None  # construction must not freeze scipy

    payload = result.to_dict()
    assert "effect_distribution" not in payload
    assert distribution._frozen is None  # serialisation must not freeze either

    import scipy.stats as sps

    cdf_zero = distribution.cdf(0.0)  # first real read freezes + delegates
    assert distribution._frozen is not None
    assert cdf_zero == sps.norm(distribution.loc, distribution.scale).cdf(0.0)


def test_lazy_normal_survives_pickle_and_deepcopy() -> None:
    """WP1 A3 regression: protocol probes on the proxy must not recurse.

    ``pickle``/``copy`` probe dunder hooks on half-initialised ``__slots__``
    instances; an unguarded ``__getattr__`` recursed through ``_materialize``
    forever. Round-trips must work and stay lazy (the frozen cache is dropped,
    not serialised).
    """
    import copy
    import pickle

    from abkit.stats.effects import LazyNormal

    proxy = LazyNormal(0.5, 2.0)
    unpickled = pickle.loads(pickle.dumps(proxy))
    assert (unpickled.loc, unpickled.scale) == (0.5, 2.0)
    assert unpickled._frozen is None  # cache is rebuilt lazily, not serialised
    assert unpickled.cdf(0.0) == proxy.cdf(0.0)

    copied = copy.deepcopy(LazyNormal(0.5, 2.0))
    assert copied.ppf(0.975) == proxy.ppf(0.975)
    assert not hasattr(proxy, "__wrapped_nonsense__")  # probes stay cheap + clean


def test_import_abkit_stats_does_not_load_statsmodels() -> None:
    """WP1 A2: the statsmodels (+pandas/patsy) import is deferred to the MDE solves."""
    import subprocess
    import sys

    code = "import sys; import abkit.stats; " "sys.exit(1 if 'statsmodels' in sys.modules else 0)"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, (
        "importing abkit.stats eagerly loaded statsmodels again (WP1 A2 regression)\n" + proc.stderr
    )


@pytest.mark.slow
@pytest.mark.skipif("ABK_BENCH" not in os.environ, reason="microbenchmark: set ABK_BENCH=1 to run")
def test_bench_normal_test_hot_path() -> None:
    """Not a gate — prints the per-call cost backing the WP1 CHANGELOG claim."""
    estimate = EffectEstimate(effect=0.03, var=0.0004)
    n = 20_000
    start = time.perf_counter()
    for _ in range(n):
        normal_test(estimate, 0.05)
    elapsed = time.perf_counter() - start
    print(f"\nnormal_test: {elapsed / n * 1e6:.2f} us/call over {n} calls")
