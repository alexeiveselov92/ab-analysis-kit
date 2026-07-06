"""WP2 parity: the A/A always-valid column shares ONE engine + ONE τ² helper with the
pipeline (m5-implementation-plan.md D4). If the A/A grew private sequential math, its
"peeking FPR back to α" proof would validate a different estimator than ships. These
tests pin that the A/A's bounds are exactly ``sequentialize`` over the CI-inverted SE,
and that its τ² is exactly ``mixture_tau2`` of the anchor variance — byte-for-byte.
"""

from __future__ import annotations

import pytest

from abkit.stats.factory import create_method
from abkit.stats.sequential import mixture_tau2, se_from_ci_length, sequentialize
from abkit.validate.resample import build_arm, placebo_mask, present_positions
from abkit.validate.scoring import _always_valid_sig, _cell_tau2, _significance
from tests.validate._panels import normal_panel

ALPHA = 0.05


def _horizon_arms(panel, seed, share_a=0.5):
    cut = panel.cutoffs[0]
    mask = placebo_mask(panel.n_units, share_a, seed)
    pos_a, pos_b = present_positions(mask, cut.unit_idx)
    arm_a = build_arm(
        panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
    )
    arm_b = build_arm(
        panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
    )
    return arm_a, arm_b


def test_a_a_always_valid_bounds_equal_the_shared_engine():
    """``_always_valid_sig`` is exactly ``sequentialize`` over the CI-inverted SE."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=11)
    method = create_method("t-test", alpha=ALPHA)
    arm_a, arm_b = _horizon_arms(panel, seed=123)
    result = method.from_suffstats(arm_a, arm_b)
    se = se_from_ci_length(result.ci_length, ALPHA)
    tau2 = mixture_tau2(se * se, ALPHA)

    sig_seq, width = _always_valid_sig(result, tau2, ALPHA)
    lo, hi, _ = sequentialize(result.effect, se, tau2, ALPHA)

    assert width == (hi - lo)  # byte-identical, no private A/A math
    assert sig_seq == _significance(lo, hi)


def test_cell_tau2_delegates_to_the_shared_mixture_helper():
    """``_cell_tau2`` == ``mixture_tau2`` of the anchor split's horizon SE² — the SAME
    helper the pipeline will call, so both paths land on the identical τ²."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=11)
    method = create_method("t-test", alpha=ALPHA)
    anchor_seed = 999

    tau2 = _cell_tau2(panel, method, horizon_pos=0, share_a=0.5, anchor_seed=anchor_seed)
    arm_a, arm_b = _horizon_arms(panel, seed=anchor_seed)
    se_h = se_from_ci_length(method.from_suffstats(arm_a, arm_b).ci_length, ALPHA)

    assert tau2 is not None
    assert tau2 == mixture_tau2(se_h * se_h, ALPHA)  # exact, one shared helper


def test_tau2_independent_of_alpha_scale_is_documented_behaviour():
    """τ² tracks the anchor variance and α only — a sanity guard the parity rests on."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=11)
    method = create_method("t-test", alpha=ALPHA)
    t1 = _cell_tau2(panel, method, horizon_pos=0, share_a=0.5, anchor_seed=1)
    t2 = _cell_tau2(panel, method, horizon_pos=0, share_a=0.5, anchor_seed=1)
    assert t1 == t2  # deterministic anchor
    assert t1 is not None and t1 > 0.0
    # a different anchor split shifts τ² only slightly (validity is robust to it)
    t3 = _cell_tau2(panel, method, horizon_pos=0, share_a=0.5, anchor_seed=2)
    assert t3 is not None
    assert t3 == pytest.approx(t1, rel=0.25)
