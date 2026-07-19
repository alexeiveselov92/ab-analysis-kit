"""The M4 milestone exit gate: ``abk validate`` proves the classic A/A failures.

An end-to-end run of the validate stack (load → resample → score → persist → chip)
over the synthetic warehouse, demonstrating the three named failures the matrix
exists to catch (aa-false-positive-matrix.md §8), each asserted with a Binomial(N,p)
band rather than point equality:

1. **Well-calibrated** — a t-test on a per-unit continuous metric (``arpu``): the
   placebo split is exchangeable, so its single-look FPR tracks α (≈5%, in budget).
2. **FPR-inflated (mis-specified)** — a z-test on a *clustered* proportion
   (``conversion``, per-unit ``nobs`` > 1): treating the pooled trials as independent
   Bernoulli underestimates the variance, so the FPR blows past budget (and the CI
   coverage collapses under the same pathology). This is the "naive proportions test
   on non-independent data" story — the A/B analog of a naive t-test on a ratio.
3. **Peeking jump** — even a correctly-specified method (``ratio-delta`` on ``ctr``)
   whose single-look FPR is in budget has a cumulative-peeking FPR (optional stopping
   over the grid) that *breaks* the budget — the hazard sequential fixes in M5 (D3/D8).

Plus the milestone plumbing: the persisted ``_ab_aa_runs`` rows carry the as-built
D15 column set at the effective per-comparison alpha, ``find_calibration`` flips the
D3 chip from ``uncalibrated`` to ``calibrated`` (over-budget on the broken cell), and
the whole run is byte-reproducible (D13).

The scaffolded seed fraction is ``nobs`` = 1, so it can't exhibit clustering; the
synthetic warehouse's ``nobs`` > 1 fraction is the mis-specified fixture. The Click
CLI vertical slice is covered by ``tests/cli/test_validate_command.py``; this gate
drives the same runner the CLI calls over a warehouse that *can* fail.
"""

from __future__ import annotations

import math

import pytest
from synthetic_ab import METRICS, PROJECT, SyntheticWarehouse, seed_cohort, seed_null_events

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._aa_runs import AA_RUN_COLUMNS
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.tuning.recompute import find_calibration, resolve_fpr_budget
from abkit.validate.persistence import aa_run_records
from abkit.validate.runner import ValidateSettings, run_validation

NOW_ISO = "2026-07-05T00:00:00"
ITERATIONS = 2000
DAYS = 14  # a 14-look daily grid: enough looks for the peeking FPR to accrue
# Binomial(N=2000, p=0.05) has σ ≈ 0.0049; a 3σ band around nominal α=0.05 is ±0.015.
# Use ±0.02 so an incidental fixture tweak doesn't red the gate on a calibrated cell.
CALIBRATED_LO, CALIBRATED_HI = 0.03, 0.07


def _matrix_experiment() -> ExperimentConfig:
    """One experiment, three main metrics at α=0.05 — the whole matrix in one run."""
    return ExperimentConfig.model_validate(
        {
            "name": "aa_matrix",
            "start_date": "2024-07-01",
            "end_date": "2024-07-14",
            "unit_key": "user_id",
            "alpha": 0.05,
            "assignment": {
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "conversion", "is_main_metric": True, "method": {"name": "z-test"}},
                {"metric": "ctr", "is_main_metric": True, "method": {"name": "ratio-delta"}},
            ],
        }
    )


def _run(warehouse, experiment):
    backend = RecomputeBackend(warehouse, experiment)
    grid = generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )
    return run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        grid,
        # inject a known effect so the power/coverage columns populate (the coverage
        # collapse on the broken cell is half the mis-specification story).
        # iterations= is EXPLICIT (bypasses the WP6 auto-N policy) and family_sweep=True
        # preserves the pre-0.2.0 default-on behavior this gate's numbers were pinned
        # under — the D9 sentinel-row assertions below still exercise the sweep.
        ValidateSettings(iterations=ITERATIONS, inject_effect=0.15, family_sweep=True),
        now_iso=NOW_ISO,
    )


@pytest.fixture(scope="module")
def gate():
    """Score the matrix once; every assertion reads the same deterministic result."""
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse, days=DAYS)
    experiment = _matrix_experiment()
    result = _run(warehouse, experiment)
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()
    for record in aa_run_records(result):
        tables.save_aa_run(record)
    cells = {cell.metric: cell for cell in result.cells}
    alphas = effective_alphas(experiment, PROJECT)
    return {
        "warehouse": warehouse,
        "experiment": experiment,
        "result": result,
        "tables": tables,
        "cells": cells,
        "alphas": alphas,
        "rows": tables.get_aa_runs("aa_matrix"),
    }


class TestValidateMatrixExitGate:
    def test_every_cell_scored_and_non_degenerate(self, gate):
        cells = gate["cells"]
        assert set(cells) == {"arpu", "conversion", "ctr"}
        for cell in cells.values():
            assert cell.status == "success", cell.error_message
            assert cell.fpr is not None, f"{cell.metric} produced no FPR"
            assert cell.details["valid_iterations"] == ITERATIONS
            assert cell.details["degenerate_horizon"] == 0

    def test_failure_a_well_calibrated_ttest_in_band(self, gate):
        """A t-test on an exchangeable per-unit sample metric tracks α — in budget."""
        cell = gate["cells"]["arpu"]
        assert CALIBRATED_LO <= cell.fpr <= CALIBRATED_HI  # 3σ band around α=0.05
        assert cell.fpr <= cell.budget  # inside the aa_fpr_budget band
        assert "well-calibrated" in cell.verdict
        # the injected-effect columns confirm the method is genuinely healthy
        assert cell.power > 0.8
        assert 0.90 <= cell.coverage <= 0.99  # CI covers the truth at ≈ nominal 95%

    def test_failure_b_clustered_proportion_ztest_inflated(self, gate):
        """A z-test on a clustered proportion (nobs>1) underestimates variance → FPR ≫ α."""
        cell = gate["cells"]["conversion"]
        assert cell.fpr > cell.budget  # out of budget…
        assert cell.fpr > 2 * cell.budget  # …by a wide, unambiguous margin
        assert "do not use" in cell.verdict
        # the SAME variance underestimate collapses CI coverage far below nominal 95%
        assert cell.coverage < 0.75

    def test_failure_c_peeking_breaks_the_budget_on_a_calibrated_method(self, gate):
        """ratio-delta on ctr is calibrated single-look, but optional stopping isn't."""
        cell = gate["cells"]["ctr"]
        assert cell.fpr <= cell.budget  # single-look: in budget
        assert cell.peeking_fpr > cell.budget  # peeking: over budget — the hazard
        assert cell.peeking_fpr > cell.fpr  # a real jump from stopping at any look

    def test_peeking_fpr_is_a_monotone_honest_superset_of_single_look(self, gate):
        """D3: peeking ⊇ single-look (horizon included), and the curve never dips."""
        for cell in gate["cells"].values():
            # peeking counts a crossing at ANY look incl. the horizon ⇒ ≥ single-look
            assert cell.peeking_fpr >= cell.fpr - 1e-12
            curve = cell.details["peeking_curve"]
            fprs = [f for _, f in curve]
            assert fprs == sorted(fprs), f"{cell.metric} peeking curve dipped: {fprs}"
            # the final look equals the reported peeking FPR (by construction)
            assert math.isclose(fprs[-1], cell.peeking_fpr, rel_tol=1e-9, abs_tol=1e-12)
        # even the well-calibrated t-test leaks materially once you peek
        arpu = gate["cells"]["arpu"]
        assert arpu.peeking_fpr > arpu.fpr + 0.02

    def test_rows_carry_the_as_built_columns_at_the_effective_alpha(self, gate):
        """D15 column set + D-critical: the persisted alpha is the chip-lookup alpha."""
        records = aa_run_records(gate["result"])
        # 3 per-metric cells + the composed-family sentinel row (D9/WP8)
        cells = [r for r in records if r["metric"] != "__family__"]
        family = [r for r in records if r["metric"] == "__family__"]
        assert len(cells) == 3 and len(family) == 1
        for record in records:
            assert set(AA_RUN_COLUMNS).issubset(record)  # save_aa_run requires every key
        assert len({r["run_id"] for r in records}) == len(records)  # unique per (invocation, row)
        alphas = gate["alphas"]
        by_metric = {r["metric"]: r for r in cells}
        for comparison in gate["experiment"].comparisons:
            expected = comparison_alpha(comparison, alphas)
            # exact equality ⇒ find_calibration's isclose(rel 1e-9) resolves, not alpha_mismatch
            assert by_metric[comparison.metric]["alpha"] == expected
        # the sentinel carries the composed FWER/FDR in its details (D9)
        import json

        fam = json.loads(family[0]["details"])["family"]
        assert fam["n_metrics"] == 3 and fam["fwer"] is not None and fam["fwer"] == fam["fdr"]

    def test_chip_flips_from_uncalibrated_to_calibrated_with_budget_verdict(self, gate):
        """D3: rows light the chip; the broken cell reads over-budget, the good ones don't."""
        rows = gate["rows"]
        experiment, alphas = gate["experiment"], gate["alphas"]
        over_budget_by_metric = {"arpu": False, "conversion": True, "ctr": False}
        for comparison in experiment.comparisons:
            metric = comparison.metric
            mcid = comparison.method.method_config_id
            alpha = comparison_alpha(comparison, alphas)
            budget = resolve_fpr_budget(PROJECT, alpha, METRICS[metric])

            before = find_calibration([], metric, mcid, alpha, budget)
            assert before.state == "uncalibrated"

            after = find_calibration(rows, metric, mcid, alpha, budget)
            assert after.state == "calibrated", metric
            assert after.fpr is not None
            assert after.over_budget is over_budget_by_metric[metric], metric

            # an alpha edit invalidates the calibration (gates like uncalibrated, D3)
            mismatch = find_calibration(rows, metric, mcid, 0.01, budget)
            assert mismatch.state == "alpha_mismatch"

    def test_worked_example_numbers_match_the_spec_table(self, gate):
        """The aa-fpr §8 worked-example table IS this fixture — pin its rendered cells.

        The spec table is framed as "the deterministic output ... pinned by the exit-gate
        e2e", so it must be a real gate: the tool's own `{:.1%}` formatter (the verdict
        string / the report's toFixed(1)) must render exactly the published percentages.
        """
        cells = gate["cells"]

        def pct(v):  # the runner/report display rounding
            return f"{v:.1%}"

        # (single-look, peeking, power, coverage) as they appear in aa-fpr §8
        expected = {
            "arpu": ("5.3%", "8.6%", "96.5%", "94.7%"),
            "conversion": ("42.4%", "43.5%", "94.7%", "55.4%"),
            "ctr": ("4.8%", "12.7%", "100.0%", "95.2%"),
        }
        for metric, (fpr_s, peek_s, power_s, cov_s) in expected.items():
            cell = cells[metric]
            assert pct(cell.fpr) == fpr_s, metric
            assert pct(cell.peeking_fpr) == peek_s, metric
            assert pct(cell.power) == power_s, metric
            assert pct(cell.coverage) == cov_s, metric

    def test_run_is_byte_reproducible(self):
        """D13: two fresh runs at identical settings reproduce every number exactly.

        Cheap on purpose (byte-reproducibility is a seed property, not a sample-size
        one) — a handful of iterations proves it without re-scoring the full matrix.
        """

        def once():
            warehouse = SyntheticWarehouse()
            seed_cohort(warehouse, n_per_arm=160)
            seed_null_events(warehouse, days=DAYS)
            experiment = _matrix_experiment()
            backend = RecomputeBackend(warehouse, experiment)
            grid = generate_grid(
                experiment.start_date,
                experiment.end_date,
                experiment.cadence_segments(),
                tz=experiment.timezone,
            )
            return run_validation(
                backend,
                experiment,
                PROJECT,
                METRICS,
                {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
                grid,
                # explicit iterations= + family_sweep=True: byte-reproducibility must
                # keep covering the composed-family rows exactly as before WP6
                ValidateSettings(iterations=200, inject_effect=0.15, family_sweep=True),
                now_iso=NOW_ISO,
            )

        a, b = once(), once()
        assert a.run_stamp == b.run_stamp  # wall-clock-free
        first = {c.metric: c for c in a.cells}
        for cell in b.cells:
            assert cell.fpr == first[cell.metric].fpr
            assert cell.peeking_fpr == first[cell.metric].peeking_fpr
            assert cell.coverage == first[cell.metric].coverage
