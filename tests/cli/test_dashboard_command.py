"""``abk dashboard`` — the DASH-6 CLI shell (m11-implementation-plan.md DASH-6).

The ``test_explore_command.py`` orchestration shape over the real ``abk init``
example + the seed-mirror warehouse, asserting what this command owes the server
DASH-3/DASH-5 built: the WHOLE selection (no one-experiment restriction), the
``metrics=``/``manager=`` fields DASH-5's Open button needs, the ``--window``
preset validated where the operator typed it, and the launcher invariants —
serves a never-run project, closes its manager, and takes NO pipeline lock.

The two real-boot tests keep the actual ``serve_dashboard``/``build_dashboard_server``
and only stub ``serve_forever`` with a ``KeyboardInterrupt`` (the
``test_dashboard_server.py`` idiom), so the page bake, the printed URL and the
job-registry teardown are the shipped code, not a fake.
"""

from __future__ import annotations

import os
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.cli.commands.dashboard as dashboard_cmd
import abkit.config.profile as profile_mod
import abkit.tuning as tuning_mod
import abkit.tuning.dashboard_server as dashboard_server
from abkit.cli.main import cli
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning.jobs import JobManager
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()

EXP = "example_signup_test"
SECOND = "second_test"


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """`abk init demo` + the seed-mirror warehouse (the M2 e2e harness)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "demo"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


@pytest.fixture
def two_experiments(scaffolded):
    """A second experiment, so the many-rows selection is real."""
    source = Path("experiments") / f"{EXP}.yml"
    clone = Path("experiments") / "second.yml"
    clone.write_text(
        source.read_text(encoding="utf-8").replace(f"name: {EXP}", f"name: {SECOND}"),
        encoding="utf-8",
    )
    return scaffolded


@pytest.fixture
def computed(scaffolded):
    """The example experiment with persisted results (one real run)."""
    result = runner.invoke(cli, ["run", "--select", EXP])
    assert result.exit_code == 0, result.output
    return scaffolded


class FakeServe:
    """Captures serve_dashboard kwargs; returns None like the real one."""

    def __init__(self, raises: BaseException | None = None):
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return None


@pytest.fixture
def serve(monkeypatch):
    fake = FakeServe()
    monkeypatch.setattr(tuning_mod, "serve_dashboard", fake)
    return fake


class TestSelection:
    def test_the_whole_selection_is_served(self, two_experiments, serve):
        """Unlike explore, many experiments is the normal case, not an error."""
        result = runner.invoke(cli, ["dashboard", "--select", "*", "--no-open"])
        assert result.exit_code == 0, result.output
        (call,) = serve.calls
        assert sorted(exp.name for _, exp in call["experiments"]) == [EXP, SECOND]
        assert "2 experiment(s) selected" in result.output

    def test_exclude_removes_a_match(self, two_experiments, serve):
        result = runner.invoke(
            cli, ["dashboard", "--select", "*", "--exclude", "second", "--no-open"]
        )
        assert result.exit_code == 0, result.output
        (call,) = serve.calls
        assert [exp.name for _, exp in call["experiments"]] == [EXP]

    def test_an_empty_selection_is_a_clean_noop(self, scaffolded, serve):
        """The unmatched-selector warning already said it; a zero-row cockpit is
        a page about nothing, so no server is built."""
        result = runner.invoke(cli, ["dashboard", "--select", "nope", "--no-open"])
        assert result.exit_code == 0, result.output
        assert "matched no experiments" in result.output
        assert "Nothing selected." in result.output
        assert serve.calls == []

    def test_a_never_run_project_still_serves(self, scaffolded, serve):
        """The DASH-6 divergence from explore's D2 noop: a project with no
        persisted rows is the dashboard's FIRST case — its rows read "no data —
        press Run", and Run is the button that fixes it."""
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "no computed results yet" not in result.output
        assert len(serve.calls) == 1

    def test_nothing_creates_internal_schema(self, scaffolded, serve):
        """A read-only launcher never writes: no `_ab_*` table is CREATED just by
        opening the cockpit on a fresh project.

        Asserted on table existence, not on row counts — ``ensure_tables()``
        creates empty tables, so "no rows" is a probe that cannot fail.
        """
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        for table in ("_ab_results", "_ab_tasks", "_ab_experiments", "_ab_unit_state"):
            assert not scaffolded.table_exists(table), f"{table} was created by the dashboard"


class TestWiring:
    def test_metrics_and_manager_reach_the_open_button(self, computed, serve):
        """DASH-5 note 2 left both fields for DASH-6 to wire: without them the
        report page loses its metric descriptions and shows a zero-unit SRM chip."""
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        (call,) = serve.calls
        assert "example_signup_cr" in call["metrics"]
        # the raw manager must be the SAME one `tables` wraps (one connection,
        # serialized by the server's db_lock)
        assert call["manager"] is call["tables"]._manager

    def test_the_window_default_and_override_are_forwarded(self, computed, serve):
        runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert serve.calls[-1]["initial_window"] == "30d"
        runner.invoke(cli, ["dashboard", "--select", EXP, "--window", "7d", "--no-open"])
        assert serve.calls[-1]["initial_window"] == "7d"

    def test_no_open_is_forwarded_and_the_default_opens(self, computed, serve):
        runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert serve.calls[-1]["open_browser"] is False
        runner.invoke(cli, ["dashboard", "--select", EXP])
        assert serve.calls[-1]["open_browser"] is True

    def test_the_profile_string_rides_along(self, computed, serve):
        """The server bakes it into the page header AND passes it to every
        spawned `abk` argv (DASH-4), so a staging cockpit cannot launch a
        production run."""
        runner.invoke(cli, ["dashboard", "--select", EXP, "--profile", "dev", "--no-open"])
        assert serve.calls[-1]["profile"] == "dev"


class TestInstalledAbkitWarning:
    """DASH-4 note 7 deferred this here: every button spawns an `abk` process, so
    an uninstalled abkit fails N jobs identically — say it once, at startup."""

    def test_silent_when_abkit_is_installed(self, computed, serve):
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "not installed in this interpreter" not in result.output

    def test_warned_once_when_neither_signal_finds_abkit(self, computed, serve, monkeypatch):
        """Both probes must fail: no dist metadata AND nothing on a `sys.path`
        stripped of the CWD (what the spawned child actually imports from)."""
        monkeypatch.setattr(
            dashboard_cmd,
            "dist_version",
            lambda _name: (_ for _ in ()).throw(PackageNotFoundError("ab-analysis-kit")),
        )
        monkeypatch.setattr(dashboard_cmd.sys, "path", ["", os.getcwd()])
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert result.output.count("not installed in this interpreter") == 1
        assert "pip install" in result.output
        # a warning, not a refusal: the page and its read-only rows still work
        assert len(serve.calls) == 1

    def test_the_probed_distribution_name_resolves(self):
        """The metadata probe hardcodes `ab-analysis-kit`. A rename would make it
        raise `PackageNotFoundError` forever — silently degrading the conjunction
        to its `sys.path` half — so pin the NAME here, where a rename fails.

        Resolvability only, deliberately not equality with ``abkit.__version__``:
        an editable install's dist-info is written once and does not track a
        later source bump, so equality would fail on a stale-but-working install
        (it reads 0.1.0 in a checkout installed before the bumps).
        """
        assert dashboard_cmd.dist_version("ab-analysis-kit")

    def test_a_pythonpath_install_is_not_warned_about(self, computed, serve, monkeypatch):
        """No dist-info, but abkit IS importable without the CWD — jobs work, so
        warning would be a false alarm (the reason for the conjunction)."""
        monkeypatch.setattr(
            dashboard_cmd,
            "dist_version",
            lambda _name: (_ for _ in ()).throw(PackageNotFoundError("ab-analysis-kit")),
        )
        pkg_parent = str(Path(dashboard_cmd.__file__).resolve().parents[3])
        monkeypatch.setattr(dashboard_cmd.sys, "path", ["", os.getcwd(), pkg_parent])
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "not installed in this interpreter" not in result.output


class TestWindowHelpStaysInLockstep:
    """`--window` is a plain string option, not a `click.Choice`: reading the
    choices at decorator time would import `tuning.overview` → numpy at
    `abk --help`, breaking the lazy-group contract. The cost is that the help
    text names the presets in prose — so pin it, or it drifts the way DASH-3
    note 10 warns a second copy of this list always does."""

    def test_help_names_every_shipped_preset_and_no_stale_one(self):
        from abkit.tuning.overview import ALL_WINDOW_PRESETS

        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0, result.output
        # the help wraps, so compare on a whitespace-collapsed line
        flat = " ".join(result.output.split())
        for preset in ALL_WINDOW_PRESETS:
            assert preset in flat, f"--window help does not name the shipped preset {preset!r}"
        for stale in ("14d", "60d", "180d", "1y"):
            assert stale not in flat, f"--window help names {stale!r}, which is not a preset"

    def test_the_documented_default_is_the_servers_default(self):
        from abkit.tuning import DEFAULT_WINDOW_PRESET

        result = runner.invoke(cli, ["dashboard", "--help"])
        assert f"[default: {DEFAULT_WINDOW_PRESET}]" in " ".join(result.output.split())


class TestGuards:
    def test_a_bad_window_exits_nonzero_naming_the_presets(self, computed):
        """The real `serve_dashboard`: `validate_window_preset` runs before a
        socket exists, and the CLI turns it into a house error line."""
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--window", "3d"])
        assert result.exit_code != 0
        assert "--window" in result.output
        assert "24h" in result.output and "90d" in result.output

    def test_outside_a_project_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["dashboard", "--no-open"])
        assert result.exit_code != 0
        assert "not inside an abkit project" in result.output

    def test_the_manager_is_closed_even_when_serving_raises(self, computed, monkeypatch):
        boom = FakeServe(raises=RuntimeError("boom"))
        monkeypatch.setattr(tuning_mod, "serve_dashboard", boom)
        closed: list[bool] = []
        monkeypatch.setattr(
            SeedMirrorWarehouse, "close", lambda self: closed.append(True), raising=False
        )
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code != 0
        assert closed == [True]


class TestRealBoot:
    """The plan's CLI-level DoD: start for real, Ctrl-C, leave nothing behind."""

    @pytest.fixture
    def interrupted(self, monkeypatch):
        """`serve_forever` immediately Ctrl-Cs, so the boot + teardown are real."""
        monkeypatch.setattr(
            dashboard_server._DashboardServer,
            "serve_forever",
            lambda self, poll_interval=0.5: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        monkeypatch.setattr(dashboard_server.webbrowser, "open", lambda url: None)

    def test_it_prints_a_tokened_url_and_shuts_the_job_registry_down(
        self, computed, interrupted, monkeypatch
    ):
        shutdowns: list[int] = []
        real_shutdown = JobManager.shutdown
        monkeypatch.setattr(
            JobManager,
            "shutdown",
            lambda self: (shutdowns.append(1), real_shutdown(self))[1],
        )
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "Dashboard: http://127.0.0.1:" in result.output
        assert "?token=" in result.output
        assert "Stopped." in result.output
        # a spawned `abk run` must not outlive the cockpit that started it
        assert shutdowns == [1]

    def test_the_dashboard_takes_no_pipeline_lock(self, computed, interrupted, monkeypatch):
        """§0.5(d): the dashboard is a launcher — only the SPAWNED subprocess
        ever locks. A lock taken here would block every `abk run` the operator
        then presses Run for."""
        locked: list[str] = []
        monkeypatch.setattr(
            InternalTablesManager,
            "acquire_lock",
            lambda self, *a, **k: locked.append("acquire"),
        )
        monkeypatch.setattr(
            InternalTablesManager,
            "release_lock",
            lambda self, *a, **k: locked.append("release"),
        )
        result = runner.invoke(cli, ["dashboard", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert locked == []
