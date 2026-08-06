"""``abk run --report`` — the WP3 CLI surface (m3-implementation-plan.md WP3/D8).

Path conventions (bare / DIR / file.html), the re-run-to-report path (zero
pending cutoffs still emits — D8), best-effort emission (a builder exception
yellow-skips and the run still exits 0 — the one recorded exception to the
exit-non-zero contract), and the guards (validate-only, one-file-many-
experiments). Runs over the ``abk init`` example against the in-memory seed
mirror (the M2 e2e harness).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()

EXP = "example_signup_test"


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """The M2 e2e harness: `abk init demo` + the seed-mirror warehouse."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "demo"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


def _assert_self_contained(path: Path) -> None:
    html = path.read_text(encoding="utf-8")
    assert "__ABK_REPORT__" in html
    assert 'id="abk-report"' in html
    for placeholder in ("__PAYLOAD__", "__REPORT_JS__", "__EXPERIMENT__"):
        assert placeholder not in html
    assert f'"experiment":"{EXP}"' in html


class TestReportPathConventions:
    def test_bare_flag_defaults_to_reports_dir(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert result.exit_code == 0, result.output
        out = Path("reports") / f"{EXP}.html"
        assert out.is_file()
        _assert_self_contained(out)
        assert f"Report → reports/{EXP}.html" in result.output

    def test_directory_value_appends_experiment_html(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--report", "readouts"])
        assert result.exit_code == 0, result.output
        out = Path("readouts") / f"{EXP}.html"
        assert out.is_file()
        _assert_self_contained(out)

    def test_html_value_is_the_exact_file(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--report", "custom/my.HTML"])
        assert result.exit_code == 0, result.output
        out = Path("custom/my.HTML")
        assert out.is_file()
        _assert_self_contained(out)

    def test_no_flag_no_emission(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert not Path("reports").exists()
        assert "Report" not in result.output


class TestReportSemantics:
    def test_rerun_with_zero_pending_cutoffs_still_emits(self, scaffolded):
        """D8: re-running an up-to-date experiment is the just-give-me-the-
        report path."""
        first = runner.invoke(cli, ["run", "--select", EXP])
        assert first.exit_code == 0, first.output
        second = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert second.exit_code == 0, second.output
        assert "cutoffs planned: 0" in second.output
        assert (Path("reports") / f"{EXP}.html").is_file()

    def test_report_line_carries_the_verdicts(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert result.exit_code == 0, result.output
        report_line = next(line for line in result.output.splitlines() if "Report →" in line)
        assert any(word in report_line for word in ("WIN", "LOSE", "FLAT", "INCONCLUSIVE"))

    def test_builder_failure_yellow_skips_and_run_exits_zero(self, scaffolded, monkeypatch):
        import abkit.reporting as reporting_mod

        def boom(*args, **kwargs):
            raise RuntimeError("payload exploded")

        monkeypatch.setattr(reporting_mod, "build_report_payload", boom)
        result = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert result.exit_code == 0, result.output
        assert "Report skipped: payload exploded" in result.output
        assert not Path("reports").exists()

    def test_never_run_experiment_skips_with_message(self, scaffolded):
        """No persisted cutoffs (steps stop before compute) — plain skip."""
        result = runner.invoke(
            cli, ["run", "--select", EXP, "--steps", "validate,plan", "--report"]
        )
        assert result.exit_code == 0, result.output
        assert "Report: no persisted results, skipped" in result.output
        assert not Path("reports").exists()


class TestReportNegativePaths:
    def test_failed_pipeline_skips_report_and_exits_nonzero(self, scaffolded, monkeypatch):
        """A failed experiment withholds the report loudly and the run still
        exits non-zero (the best-effort clause never masks pipeline failure)."""

        orig = scaffolded.execute_query

        def explode(query, params=None):
            # fail only the warehouse SELECTs so the failure lands inside the
            # per-experiment pipeline (outcome=failed), not the driver setup
            if "example_ab_assignments" in query:
                raise RuntimeError("warehouse down")
            return orig(query, params)

        monkeypatch.setattr(scaffolded, "execute_query", explode)
        result = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert result.exit_code != 0
        assert "Report skipped: experiment failed" in result.output
        assert "Report →" not in result.output
        assert not Path("reports").exists()

    def test_subdirectory_invocation_anchors_bare_to_root_and_value_to_cwd(
        self, scaffolded, monkeypatch
    ):
        """Pin the donor asymmetry: bare --report anchors to the project root;
        an explicit DIR value is cwd-relative (review finding — behavior was
        unpinned)."""
        first = runner.invoke(cli, ["run", "--select", EXP])
        assert first.exit_code == 0, first.output
        root = Path.cwd()
        monkeypatch.chdir(root / "experiments")

        bare = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert bare.exit_code == 0, bare.output
        assert (root / "reports" / f"{EXP}.html").is_file()

        valued = runner.invoke(cli, ["run", "--select", EXP, "--report", "readouts"])
        assert valued.exit_code == 0, valued.output
        assert (root / "experiments" / "readouts" / f"{EXP}.html").is_file()


class TestReportGuards:
    def test_validate_only_with_report_is_rejected(self, scaffolded):
        result = runner.invoke(cli, ["run", "--steps", "validate", "--report"])
        assert result.exit_code != 0
        assert "--report needs pipeline steps" in result.output

    def test_single_file_with_many_experiments_is_rejected(self, scaffolded):
        source = Path("experiments") / f"{EXP}.yml"
        clone = Path("experiments") / f"{EXP}_b.yml"
        clone.write_text(
            source.read_text(encoding="utf-8").replace(f"name: {EXP}", f"name: {EXP}_b", 1),
            encoding="utf-8",
        )
        result = runner.invoke(cli, ["run", "--report", "one.html"])
        assert result.exit_code != 0
        assert "names one file" in result.output


class TestTheSummaryLine:
    """m14 DEC-4 (audit gap 7): the line labels its verdicts and names a leader.

    Through `0.8.0` it joined bare words, so at three arms the reader could not
    tell which arm each belonged to. The arm-vs-arm verdicts DEC-3 added stay
    OFF this line — they are evidence, and a one-line summary is the wrong
    place to explain the difference.
    """

    @staticmethod
    def _payload(*roles: str, arms: int = 3, rollups=None) -> dict:
        words = ["WIN", "FLAT", "LOSE", "INCONCLUSIVE"]
        treatments = ["t1", "t2", "t3", "t4"]
        return {
            "arms": [f"a{i}" for i in range(arms)],
            "rollups": rollups if rollups is not None else [],
            "verdicts": [
                {"verdict": word, "role": role, "pair": {"c": "control", "t": arm}}
                for word, role, arm in zip(words, roles, treatments, strict=False)
            ],
        }

    def test_ship_decisions_carry_their_arm_and_arm_pairs_stay_off(self):
        from abkit.cli.commands.run import _verdict_note

        note = _verdict_note(self._payload("vs_control", "vs_control", "treatment_pair"))
        assert note == "WIN t1 · FLAT t2"

    def test_the_leader_replaces_the_guesswork(self):
        from abkit.cli.commands.run import _verdict_note

        note = _verdict_note(
            self._payload(
                "vs_control",
                "vs_control",
                rollups=[{"metric": "revenue", "leader": "t1"}],
            )
        )
        assert note == "WIN t1 · FLAT t2 · leader: t1 (revenue)"

    def test_disagreeing_metrics_both_get_named(self):
        """The readout REPORTS the disagreement and does not break the tie
        (D2 — there is no declared metric priority), so neither may the line."""
        from abkit.cli.commands.run import _verdict_note

        note = _verdict_note(
            self._payload(
                "vs_control",
                "vs_control",
                rollups=[
                    {"metric": "revenue", "leader": "t1"},
                    {"metric": "orders", "leader": "t2"},
                ],
            )
        )
        assert note.endswith("leader: t1 (revenue), t2 (orders)")

    def test_no_leader_prints_no_leader_clause(self):
        from abkit.cli.commands.run import _verdict_note

        note = _verdict_note(
            self._payload("vs_control", rollups=[{"metric": "revenue", "leader": None}])
        )
        assert note == "WIN t1"

    def test_a_two_arm_line_is_the_0_8_0_line(self):
        """The §0.2 leg: one treatment means the arm suffix names the only arm
        there is, so the line stays exactly what `0.8.0` printed."""
        from abkit.cli.commands.run import _verdict_note

        note = _verdict_note(
            self._payload("vs_control", arms=2, rollups=[{"metric": "revenue", "leader": "t1"}])
        )
        assert note == "WIN"

    def test_a_pre_0_9_0_payload_has_no_role_and_is_all_ship_decisions(self):
        """Every list baked before 0.9.0 is control-anchored by construction —
        a missing key must not silence the line, and an absent `arms` key (no
        such payload exists, but the reader must not raise) reads as two arms."""
        from abkit.cli.commands.run import _verdict_note

        assert (
            _verdict_note({"verdicts": [{"verdict": "WIN"}, {"verdict": "LOSE"}]}) == "WIN · LOSE"
        )

    def test_the_printed_line_actually_goes_through_the_hold(self, scaffolded, monkeypatch):
        """The helper is only a hold if the surface still CALLS it.

        Reverting `_emit_experiment_report` to the pre-DEC-3 inline join —
        the exact leak this WP declares it prevents — left every test above
        green, because they all call `_verdict_note` directly. This one reads
        the terminal.
        """
        import abkit.cli.commands.run as run_mod

        monkeypatch.setattr(run_mod, "_verdict_note", lambda payload: "HELD-BY-THE-SEAM")
        result = runner.invoke(cli, ["run", "--select", EXP, "--report"])
        assert result.exit_code == 0, result.output
        report_line = next(line for line in result.output.splitlines() if "Report →" in line)
        assert "HELD-BY-THE-SEAM" in report_line

    def test_an_experiment_with_only_treatment_pairs_says_so(self):
        """Unreachable through `evaluate` (a declared pair set always contains
        the control pairs), but the empty-list branch must not print an empty
        parenthetical if it ever is."""
        from abkit.cli.commands.run import _verdict_note

        assert _verdict_note(self._payload("treatment_pair")) == "no verdicts yet"
