"""m13 STAT-3: the A/A instrument DEGRADES on an asymmetric interval — it does not fail.

STAT-3a shipped the opposite contract, and it was right for the world it shipped
into: no registered method could declare ``asymmetric_ci``, so "a failed cell
carrying its reason" was the loudest honest answer to a configuration nobody had.
STAT-3 ships the first method that CAN declare it, and that inverts the argument.
``_cell_tau2`` is the first substantive statement of both scoring engines and is
run unconditionally — the D8 peeking column is measured side-by-side even with
``sequential.enabled`` off — so a refusal there fails **every** cell of the
comparison. The consequences were not local:

- `abk validate` is the instrument ``contributing.md``'s change-control names for
  exactly this kind of deviation, and it would refuse to measure the estimator it
  was invoked to certify;
- ``find_calibration`` counts only ``status == "success"`` rows, so explore's D3
  chip could never leave ``uncalibrated`` — and the command it tells the operator
  to run is the one that cannot clear it.

The right answer is the one bootstrap already gets from ``supports_sequential =
False``: score the fixed columns, omit the sequential one, and say why. The guard
itself is untouched — this file's last test proves the inversion still refuses, so
removing the gate cannot silently re-enable the mis-recovery it exists to stop.

Reaching the flag needs a method that declares it; the shipped one is ``z-test``
with ``interval: score`` (a param, hence per-instance), and the t-test probes below
flip the attribute directly to keep the panel fixtures on one input kind.
"""

from __future__ import annotations

import pytest
from synthetic_ab import SyntheticWarehouse, seed_cohort, seed_null_events

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.method_config import MethodConfig
from abkit.stats.exceptions import AsymmetricCIError, StatsError
from abkit.stats.registry import get_method_class
from abkit.stats.sequential import se_from_ci_length
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
def test_score_cell_scores_the_fixed_columns_and_omits_the_sequential_one(vectorized: bool):
    """Both engines. The FPR — the column the whole matrix exists for — must survive.

    The symmetric twin is scored in the same test so the sequential columns being
    ``None`` is a DIFFERENCE, not a property of the fixture: without it, a panel
    that never anchored τ² would make this pass against a no-op gate.
    """
    panel = normal_panel(n_units=1200, n_cutoffs=2, seed=7)
    kwargs = {"iterations": 50, "seed_parts": ("aa", "x")}

    symmetric = score_cell(panel, _ttest(supports_vectorized=vectorized), **kwargs)
    assert symmetric.fpr_sequential is not None
    assert symmetric.peeking_fpr_sequential is not None

    asymmetric = score_cell(
        panel, _ttest(asymmetric_ci=True, supports_vectorized=vectorized), **kwargs
    )
    assert asymmetric.fpr is not None
    assert asymmetric.valid_iterations == symmetric.valid_iterations
    assert asymmetric.fpr == symmetric.fpr  # the fixed columns are untouched
    assert asymmetric.fpr_sequential is None
    assert asymmetric.peeking_fpr_sequential is None
    assert asymmetric.peeking_curve_sequential == ()


@pytest.mark.parametrize("vectorized", [True, False])
def test_the_skipped_column_says_WHY_and_not_the_other_reason(vectorized: bool):
    """ "τ² could not be anchored (degenerate horizon)" is true of a degenerate panel
    and false here — nothing was anchored because nothing was attempted. Two engines
    carry this note, so it lives in one helper; a catch-all string would let the two
    reasons be confused exactly where an operator is deciding whether their data is
    the problem."""
    panel = normal_panel(n_units=1200, n_cutoffs=2, seed=7)
    score = score_cell(
        panel,
        _ttest(asymmetric_ci=True, supports_vectorized=vectorized),
        iterations=50,
        seed_parts=("aa", "x"),
    )
    note = " ".join(score.warnings)
    assert "asymmetric" in note
    assert "τ² could not be anchored" not in note


def test_family_sweep_degrades_the_same_way():
    """The composed sweep's always-valid peeking column runs the same inversion, and
    the composed FWER/FDR — what `--family-sweep` is for — must still be measured."""
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
    result = sweep_family(
        members,
        correction="bonferroni",
        iterations=50,
        share_a=0.5,
        seed_parts=("f",),
        # what the runner passes: the peeking pair is composed side-by-side always
        sequential=True,
    )
    assert result.fwer is not None
    assert result.fwer_sequential is None


def test_the_cell_is_reported_as_a_SUCCESS_so_the_calibration_chip_can_go_green(monkeypatch):
    """The operational point, end to end through the runner.

    A ``failed`` cell carries ``fpr=None``, and ``find_calibration`` counts only
    successful rows — so the explore D3 gate would read ``uncalibrated`` forever and
    the command it names could never clear it. This is the assertion that would have
    caught the defect: it is about the row an operator's cockpit reads, not about an
    exception type.
    """
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
    assert cell.status == "success"
    assert cell.fpr is not None
    assert not cell.error_message
    assert cell.fpr_sequential is None


def test_the_inversion_ITSELF_still_refuses():
    """The gate in ``_cell_tau2`` is a routing decision, not a relaxation of the
    guard. If someone deletes it, this stays green and the tests above go red —
    which is the shape that tells the next reader the two are different claims."""
    assert issubclass(AsymmetricCIError, StatsError)
    with pytest.raises(AsymmetricCIError):
        se_from_ci_length(0.4, ALPHA, method=_ttest(asymmetric_ci=True))
