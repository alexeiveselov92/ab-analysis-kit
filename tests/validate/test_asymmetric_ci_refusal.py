"""m13 STAT-3a: the A/A instrument refuses an asymmetric interval instead of scoring it.

Seven of the eleven SE-by-CI-inversion entry points are inside `abk validate` — its
per-cell sequential column (both engines) and the composed family sweep. That is the
half that matters most: the instrument would not merely fail to SEE a mis-recovered
standard error, it would compute the peeking FPR on one and then certify the method as
calibrated (docs/specs/m13-implementation-plan.md §6a).

Reaching the guard needs a method that declares the flag, and none of the twelve does
(the roster gate in tests/stats/sequential/test_asymmetric_ci_guard.py keeps it that
way), so these tests flip it on a shipped class/instance — the same probe STAT-3's own
method will make real.
"""

from __future__ import annotations

import pytest
from synthetic_ab import SyntheticWarehouse, seed_cohort, seed_null_events

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.method_config import MethodConfig
from abkit.stats.exceptions import AsymmetricCIError, StatsError
from abkit.stats.registry import get_method_class
from abkit.validate.family import FamilyMember, sweep_family
from abkit.validate.runner import ValidateSettings, run_validation
from abkit.validate.scoring import score_cell
from tests.validate._panels import normal_panel
from tests.validate.test_runner import METRICS, NOW_ISO, PROJECT, _grid, make_experiment

ALPHA = 0.05


def _ttest(**flags):
    method = MethodConfig(name="t-test", params={"test_type": "absolute"}).bind(alpha=ALPHA)
    for name, value in flags.items():
        setattr(method, name, value)
    return method


@pytest.mark.parametrize("vectorized", [True, False])
def test_score_cell_refuses_both_engines(vectorized: bool) -> None:
    """Neither the batch kernel nor the scalar fallback may score the sequential column."""
    panel = normal_panel(n_units=1200, n_cutoffs=2, seed=7)
    method = _ttest(asymmetric_ci=True, supports_vectorized=vectorized)

    with pytest.raises(AsymmetricCIError) as exc:
        score_cell(panel, method, iterations=50, seed_parts=("aa", "x"))
    assert "t-test" in str(exc.value)


def test_family_sweep_refuses() -> None:
    """The composed sweep's always-valid peeking column runs the same inversion."""
    members = [
        FamilyMember(
            metric=f"m{i}",
            panel=normal_panel(n_units=1200, n_cutoffs=2, seed=100 + i),
            method=_ttest(asymmetric_ci=True),
            alpha=ALPHA,
            planted=False,
        )
        for i in range(2)
    ]
    with pytest.raises(AsymmetricCIError):
        sweep_family(
            members,
            correction="bonferroni",
            iterations=50,
            share_a=0.5,
            seed_parts=("f",),
            # what the runner passes: the peeking pair is composed side-by-side always
            sequential=True,
        )


def test_the_refusal_reaches_the_operator_as_a_failed_cell(monkeypatch) -> None:
    """Per-cell isolation turns it into a REPORTED failure, not a silent missing column.

    ``AsymmetricCIError`` is a ``StatsError``, which is what the runner's per-cell net
    already catches (the m4 F1 bootstrap precedent), so the matrix survives while the
    cell carries its reason — the row an operator reads, not a decision_log entry the
    CLI never prints (the M7 WP6 lesson).
    """
    assert issubclass(AsymmetricCIError, StatsError)
    # the REGISTRY's class: tests/stats/test_registry_factory.py reloads the ttest
    # module, after which an imported `TTest` is an object nothing resolves to
    monkeypatch.setattr(get_method_class("t-test"), "asymmetric_ci", True)

    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=140)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_asym", "arpu", {"name": "t-test"})

    result = run_validation(
        RecomputeBackend(warehouse, experiment),
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=100),
        now_iso=NOW_ISO,
    )

    cell = result.cells[0]
    assert cell.status == "failed"
    assert cell.fpr is None
    assert "asymmetric" in (cell.error_message or "").lower()
    assert "asymmetric" in cell.verdict.lower()
