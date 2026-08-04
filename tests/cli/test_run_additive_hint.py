"""PERF-1: `abk run` says the additive read path exists.

The m9 fast path shipped silent — it lived only in `cumulative-intervals.md`,
so a scaffolded project paid the STATE write and never took the read and
nothing ever mentioned it. These drive the real CLI over the real scaffold:
the hint has to reach the TERMINAL (the M7 `decision_log` lesson — a list
nothing echoes is a list nobody reads), and `--cost-report` has to print the
measured day-additive slice beside it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from tests._helpers.scaffold import set_incremental_reads, unset_incremental_reads
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"
PROJECT_YML = Path("abkit_project.yml")


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """The scaffold, wired to the in-memory seed mirror. Not run yet — these
    tests care about what the FIRST run says."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


def _run(*extra: str):
    result = runner.invoke(cli, ["run", "--select", EXP, *extra])
    assert result.exit_code == 0, result.output
    return result.output


class TestTheHintReachesTheTerminal:
    def test_undecided_project_is_told_the_fast_path_exists(self, scaffolded):
        unset_incremental_reads(PROJECT_YML)
        output = _run()
        assert "compute.incremental_reads is unset" in output
        # the scaffold's example_arpu is the one day-additive comparison
        assert "1 of 2 comparisons are day-additive" in output
        assert "verify-incremental" in output

    def test_the_scaffold_itself_is_quiet(self, scaffolded):
        """PERF-1 flipped the scaffold to `true`, so the project abkit writes
        must not nag about its own configuration."""
        output = _run()
        assert "compute.incremental_reads is unset" not in output

    def test_an_explicit_false_is_a_decision_and_silences_it(self, scaffolded):
        """`declared` has to come from whether the KEY was written, not from
        the resolved boolean — an absent block and an explicit `false` resolve
        to the SAME value and must behave differently, or the nag never ends.
        """
        set_incremental_reads(PROJECT_YML, False)
        output = _run("--cost-report")
        assert "compute.incremental_reads is unset" not in output
        # ...and it really is the recompute path being kept quiet
        assert "would read day moments" in output


class TestTheSeriesLengthIsTheGridNotTheSum:
    """`--full-refresh` re-opens already-computed cutoffs, so `pending` and
    `computed` OVERLAP. Summing them counted every refreshed look twice."""

    def test_a_refresh_does_not_inflate_the_reported_series(self, scaffolded):
        unset_incremental_reads(PROJECT_YML)
        assert "series is 14 looks long" in _run()

        refreshed = _run("--full-refresh", "--from", "2024-07-01", "--to", "2024-07-15", "--force")
        assert "series is 14 looks long" in refreshed, refreshed
        assert "27 looks" not in refreshed

    def test_a_short_series_is_not_nagged_into_existence_by_a_refresh(self, scaffolded):
        """The harm the inflation actually does: a 4-look series is below the
        6-look threshold, but double-counting pushed it over and produced a
        false nag on exactly the series the threshold exists to keep quiet."""
        unset_incremental_reads(PROJECT_YML)
        path = Path("experiments") / f"{EXP}.yml"
        document = yaml.safe_load(path.read_text())
        document["horizon_ts"] = "2024-07-05"  # 4 looks from 2024-07-01
        path.write_text(yaml.safe_dump(document, sort_keys=False))

        assert "compute.incremental_reads is unset" not in _run()
        refreshed = _run("--full-refresh", "--from", "2024-07-01", "--to", "2024-07-05", "--force")
        assert "compute.incremental_reads is unset" not in refreshed, refreshed


class TestFallbackExtentIsReported:
    """The `on_fallback` wiring, end to end. Deleting the `on_fallback=` line
    in the driver left every other test in the suite green — the extent counter
    was reachable only by hand-constructing the dataclass."""

    def _run_without_state(self):
        # `_ab_unit_state` stays empty, so every eligible read hits the gap
        # check and falls back — the reader's own warnings say WHY, and only
        # the counter can say how many looks paid for it.
        return _run("--steps", "validate,plan,load,compute", "--cost-report")

    def test_every_look_falling_back_is_counted_and_named(self, scaffolded):
        output = self._run_without_state()
        assert "fell back to full recompute for 14 of 14 looks this run" in output
        assert "0 of 14 looks took the additive path" in output

    def test_the_count_never_exceeds_the_looks_computed(self, scaffolded):
        """The sequential τ² anchor scan also calls `load_cutoff`, on cutoffs
        that are NOT in `pending`. Counting those made `fallbacks` outrun
        `looks_computed` — "fell back for 15 of 14 looks", and a NEGATIVE
        served count on the line below it."""
        document = yaml.safe_load((Path("experiments") / f"{EXP}.yml").read_text())
        document["sequential"] = {"enabled": True}
        (Path("experiments") / f"{EXP}.yml").write_text(yaml.safe_dump(document, sort_keys=False))

        output = self._run_without_state()
        assert "fell back to full recompute for 14 of 14 looks" in output
        assert "15 of 14" not in output
        # The negative served count is asserted on the LINES THAT CARRY COUNTS,
        # never over the whole output: `"-1" not in output` also matched pytest's
        # own tmpdir (`pytest-12/test_…`), so the test passed while the temp
        # counter was 0–9 and failed from 10 onward — a gate that fails for a
        # reason unrelated to the code, and increasingly often, since the counter
        # only grows. CI stayed green because its machines are fresh.
        count_lines = [ln for ln in output.splitlines() if "looks" in ln]
        assert count_lines, "the hint lines went missing — the assertion below would be vacuous"
        for line in count_lines:
            assert "-1" not in line, line


class TestTheCostReportCounterfactual:
    def test_recompute_run_prints_what_the_fast_path_would_read(self, scaffolded):
        set_incremental_reads(PROJECT_YML, False)
        output = _run("--cost-report")
        assert "day-additive" in _cost_lines(output)
        assert "would read day moments for those 14 looks" in output

    def test_incremental_run_prints_how_many_looks_took_the_path(self, scaffolded):
        output = _run("--cost-report")
        assert "day-additive" in _cost_lines(output)
        assert "14 of 14 looks took the additive path" in output

    def test_the_slice_excludes_a_non_additive_comparison(self, scaffolded):
        """The slice must be attributed, not just present. `example_signup_cr`
        projects `max()` + a literal, so it is deliberately NOT day-additive —
        a run of only that metric must print no slice at all.

        This is the structural half of "subset, never sibling": asserting the
        rendered wall-clock instead passes against an implementation that puts
        EVERY comparison in the slice, because whether the additive line reads
        lower than `compute:` is then a timing accident.
        """
        set_incremental_reads(PROJECT_YML, False)
        lines = _cost_lines(_run("--cost-report", "--metric", "example_signup_cr"))
        assert "compute" in lines
        assert "day-additive" not in lines

    def test_the_slice_is_a_subset_of_the_stage_never_a_sibling(self, scaffolded):
        """`compute.additive` is a SLICE of `compute`: an eligible look records
        into both. The sibling implementation — recording an eligible look into
        `compute.additive` INSTEAD of `compute` — drops the stage line entirely
        for an all-additive run, which is what this catches."""
        set_incremental_reads(PROJECT_YML, False)
        lines = _cost_lines(_run("--cost-report", "--metric", "example_arpu"))
        assert "compute" in lines, "the stage total must survive an all-additive run"
        assert "day-additive" in lines

    def test_without_the_flag_nothing_is_printed(self, scaffolded):
        output = _run()
        assert "of which day-additive" not in output
        assert "cost:" not in output


#: the `--cost-report` child lines, by stage label — the tree prefix stripped
_COST_LABELS = {"load:": "load", "state:": "state", "compute:": "compute"}
_COST_LABELS["of which day-additive:"] = "day-additive"


def _cost_lines(output: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.lstrip(" │┌└─")
        for label, name in _COST_LABELS.items():
            if line.startswith(label):
                found[name] = line[len(label) :].strip()
    return found
