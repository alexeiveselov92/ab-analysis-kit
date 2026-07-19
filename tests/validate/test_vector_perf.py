"""M7 WP5 — the executable performance gate for the vectorized scoring engine.

The track's founding lesson is that a rule without an executable gate does not
hold (the 800k-pass nested loop lived *inside* a numpy-first codebase) — so the
milestone's perf claim is asserted here, in CI, on the REPORT reference shape:
**2 methods × 2000 iterations × 100 grid cutoffs** (t-test and CUPED, 1000
units, with an injected pass — the full null + injected + sequential work).

The bound is deliberately generous (``< 10 s`` per cell) so it absorbs loaded
CI runners without flaking; the honest measured numbers live in the
CHANGELOG/PR, never in the assertion (m7-implementation-plan.md §WP5 step 3 —
an aspirational hard bound would get skipped/silenced over time and stop
gating anything). Measured on the dev machine (WSL2, OpenBLAS): ~1.3–1.7 s per
cell bare, **~2.2–2.5 s under coverage instrumentation** — and the CI Test job
runs pytest WITH ``--cov=abkit`` (the default addopts; only the e2e job opts
out), so the bound is sized against the coverage-on number (~4× headroom),
not the bare one (adversarial review round 1: a 5 s bound left only ~2× under
the tracer). The same shape through the scalar engine is ~25 s (the WP4 ~10×
record), so a silent fallback to the scalar path fails this gate on time alone
— and fails loudly first, via the monkeypatched scalar engine below.
"""

from __future__ import annotations

import time

import pytest

import abkit.validate.scoring as scoring
from abkit.stats.factory import create_method
from abkit.validate.scoring import score_cell
from tests.validate._panels import normal_panel

#: CI-safe wall-clock bound per reference cell — sized against the ~2.2–2.5 s
#: coverage-instrumented dev measurement (the CI Test job traces `--cov=abkit`).
BOUND_SECONDS = 10.0
ITERATIONS = 2000  # the runner's DEFAULT_ITERATIONS — the REPORT reference N
N_CUTOFFS = 100
N_UNITS = 1000
SEED_PARTS = ("wp5", "perf")


@pytest.fixture(scope="module", autouse=True)
def _warmup():
    """Absorb one-time costs outside the timed region: lazy statsmodels (the
    MDE path), scipy special dispatch, BLAS thread-pool spin-up."""
    score_cell(
        normal_panel(n_units=64, n_cutoffs=3, seed=1),
        create_method("t-test", alpha=0.05),
        iterations=8,
        seed_parts=SEED_PARTS,
    )


@pytest.fixture(autouse=True)
def _vectorized_engine_only(monkeypatch):
    """The gate times the vectorized engine — if dispatch ever regresses to the
    scalar loop (e.g. a method silently losing ``supports_vectorized``), fail
    with THIS message, not a confusing 25 s timeout."""

    def _boom(*args, **kwargs):
        raise AssertionError(
            "the perf gate must run the vectorized engine, not the scalar fallback"
        )

    monkeypatch.setattr(scoring, "_score_cell_scalar", _boom)


@pytest.mark.parametrize(
    ("method_name", "with_covariate", "panel_seed"),
    [("t-test", False, 11), ("cuped-t-test", True, 12)],
)
def test_reference_cell_completes_within_ci_bound(method_name, with_covariate, panel_seed):
    panel = normal_panel(
        n_units=N_UNITS, n_cutoffs=N_CUTOFFS, seed=panel_seed, with_covariate=with_covariate
    )
    method = create_method(method_name, alpha=0.05)

    started = time.perf_counter()
    score = score_cell(
        panel, method, iterations=ITERATIONS, seed_parts=SEED_PARTS, inject_effect=0.05
    )
    elapsed = time.perf_counter() - started

    # The timing must cover real work — a degenerate panel scoring nothing
    # would "pass" any bound.
    assert score.valid_iterations == ITERATIONS
    assert score.power is not None and score.coverage is not None
    assert score.tau2 is not None  # the sequential twin ran too

    assert elapsed < BOUND_SECONDS, (
        f"{method_name}: reference cell (2000 it × 100 cutoffs × {N_UNITS} units) took "
        f"{elapsed:.2f}s — over the {BOUND_SECONDS:.0f}s CI bound (dev baseline ~2.5s "
        "with coverage tracing on; a regression this large usually means a python-level "
        "loop crept back into the block-streamed path, or dispatch fell back to the "
        "scalar engine)"
    )
