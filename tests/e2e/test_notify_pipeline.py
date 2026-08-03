"""The M12 exit gate: every notification signal, through the real CLI.

``docs/specs/m12-implementation-plan.md`` NTF-6. The per-WP suites pin each
signal in isolation; this one runs the whole feature the way an operator does —
a scaffolded project, three experiments in one `abk run --notify`, channels
declared in `profiles.yml`, routing declared in experiment YAML — and asserts
the four claims the milestone is worth nothing without:

1. **A scheduled run is quiet.** The same command over unchanged data sends
   once, not once per run — proved through the real `_ab_notify_states` table
   across separate CLI invocations, not an in-process cache.
2. **The urgent signals reach an on-call channel while routine ones do not.**
   One broken sample split and one failed pipeline, on a channel scoped to
   `on: [srm, error]`, in the same run that stays silent there about the
   healthy experiment.
3. **No channel can change an exit code.** A channel that raises on every send
   is injected alongside a working one: the healthy run still exits 0, the
   failing experiment still exits non-zero, and the working channel still
   receives everything.
4. **Every kind actually fires.** All six of `SignalKind` are observed on a
   channel that accepts only that kind — the gate against a vocabulary whose
   entries are aspirational.

The warehouse is ``test_first_run``'s seed mirror, so the SQL the scaffold
ships is really rendered and aggregated.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.config.signals import SIGNAL_KINDS
from abkit.notify.base import BaseChannel, ReadoutData
from abkit.notify.factory import ChannelFactory
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()

HEALTHY = "example_signup_test"
SRM_BROKEN = "srm_broken_test"
DOOMED = "doomed_test"
#: the marker the doomed experiment injects into its own assignment SQL, so the
#: warehouse can fail exactly that experiment inside a multi-experiment run
BOOM = "boom_sentinel"


class SpyChannel(BaseChannel):
    """Records every delivery; ``mode='raise'`` is the fail-soft injection."""

    sent: list[tuple[str, ReadoutData]] = []

    def __init__(self, label: str = "spy", mode: str = "ok"):
        self.label = label
        self.mode = mode

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        if self.mode == "raise":
            raise RuntimeError("this channel is on fire")
        SpyChannel.sent.append((self.label, readout))
        return True


def kinds_on(label: str) -> list[str]:
    """Every payload kind *label* received, in order."""
    return [payload.kind for name, payload in SpyChannel.sent if name == label]


def write_yaml(path: Path, document: dict) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def configure_channels(**channels) -> None:
    path = Path("profiles.yml")
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles["notification_channels"] = channels
    write_yaml(path, profiles)


def experiment_yaml(name: str) -> dict:
    return yaml.safe_load(Path("experiments", f"{name}.yml").read_text(encoding="utf-8"))


def clone_experiment(name: str, **overrides) -> None:
    """A second experiment off the scaffolded one, sharing its metrics.

    The scaffold's assignment SQL carries no experiment predicate, so every
    clone reads the same cohort — which is what makes a THREE-experiment run
    testable at all without a second synthetic warehouse.
    """
    document = experiment_yaml(HEALTHY)
    document["name"] = name
    document.update(overrides)
    write_yaml(Path("experiments", f"{name}.yml"), document)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")

    warehouse = SeedMirrorWarehouse()
    original = warehouse.execute_query

    def failing(query, params=None):
        # the doomed experiment's OWN queries carry its added_filters marker;
        # nothing else in the project does, so one run can hold a failure and
        # two successes at once
        if BOOM in query:
            raise RuntimeError("warehouse down")
        return original(query, params)

    monkeypatch.setattr(warehouse, "execute_query", failing)
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))

    # a split the data cannot satisfy — the seed mirror assigns 50/50, so the
    # SRM gate fails on real counts rather than on a stubbed flag
    clone_experiment(
        SRM_BROKEN,
        assignment={
            **experiment_yaml(HEALTHY)["assignment"],
            "expected_split": {"control": 0.9, "treatment": 0.1},
        },
    )
    clone_experiment(
        DOOMED,
        assignment={
            **experiment_yaml(HEALTHY)["assignment"],
            "added_filters": f"AND user_id != '{BOOM}'",
        },
    )

    SpyChannel.sent = []
    monkeypatch.setitem(ChannelFactory.CHANNEL_TYPES, "spy", SpyChannel)
    return warehouse


def run_notify(*args: str):
    return runner.invoke(cli, ["run", "--notify", *args])


class TestTheWholeFeature:
    def test_one_run_routes_every_experiment_to_the_channel_that_asked_for_it(self, project):
        configure_channels(
            team={"type": "spy", "label": "team", "on": ["readout"]},
            oncall={"type": "spy", "label": "oncall", "on": ["srm", "error"]},
        )

        result = run_notify()

        # the doomed experiment fails, so the RUN fails — notifying never
        # rescues an exit code
        assert result.exit_code != 0, result.output
        # routine readouts to the team channel only: the healthy experiment and
        # the SRM one (whose readout is ALSO a readout), never the failure
        assert kinds_on("team") == ["readout", "readout"]
        # the on-call channel hears the broken split and the crash, and stays
        # quiet about the healthy experiment
        assert sorted(kinds_on("oncall")) == ["error", "readout"]
        urgent = {payload.experiment for name, payload in SpyChannel.sent if name == "oncall"}
        assert urgent == {SRM_BROKEN, DOOMED}
        # the SRM message is ONE message re-classified, not a second one
        srm_payload = next(
            payload
            for name, payload in SpyChannel.sent
            if name == "oncall" and payload.experiment == SRM_BROKEN
        )
        assert srm_payload.srm_flag is True
        assert sum(1 for _, p in SpyChannel.sent if p.experiment == SRM_BROKEN) == 2

    def test_a_second_identical_run_is_silent_about_what_it_already_said(self, project):
        """The milestone's core value claim, across two CLI invocations: the
        state that keeps a scheduler quiet lives in the warehouse, not in a
        process."""
        configure_channels(team={"type": "spy", "label": "team", "on": ["readout"]})

        first = run_notify("--select", HEALTHY)
        delivered = len(SpyChannel.sent)
        second = run_notify("--select", HEALTHY)

        assert (first.exit_code, second.exit_code) == (0, 0)
        assert delivered == 1
        assert len(SpyChannel.sent) == 1  # nothing new
        assert "unchanged" in second.output
        # and the memory is really in the table
        rows = project.execute_query("SELECT * FROM _ab_notify_states")
        assert [r["notify_count"] for r in rows] == [1]

    def test_a_burning_channel_changes_nothing_for_anyone(self, project):
        """§0.4 point 1, the single most important fail-soft proof in the
        track: a channel raising on every send must not alter an exit code, and
        must not stop the channel beside it."""
        configure_channels(
            fire={"type": "spy", "label": "fire", "mode": "raise"},
            team={"type": "spy", "label": "team"},
        )

        healthy = run_notify("--select", HEALTHY)
        doomed = run_notify("--select", DOOMED)

        assert healthy.exit_code == 0, healthy.output
        assert doomed.exit_code != 0, doomed.output  # the PIPELINE failed, not the notify
        assert kinds_on("fire") == []  # it raised before recording anything
        assert kinds_on("team") == ["readout", "error"]

    def test_a_message_no_channel_accepted_is_retried_next_run(self, project):
        """The dedup may only remember what was actually delivered — otherwise
        one outage silences a verdict permanently, since nothing re-derives an
        unsent message."""
        configure_channels(fire={"type": "spy", "label": "fire", "mode": "raise"})
        assert run_notify("--select", HEALTHY).exit_code == 0

        configure_channels(team={"type": "spy", "label": "team"})
        assert run_notify("--select", HEALTHY).exit_code == 0

        assert kinds_on("team") == ["readout"]


class TestEverySignalKindFires:
    """One channel per kind, each accepting only that kind (m12 NTF-6).

    The gate against a vocabulary that outgrew its implementation: NTF-1..5
    each shipped kinds `SignalKind` had already declared, and `verdict_change`
    spent four work packages accepted-but-never-emitted.
    """

    def test_readout_srm_and_error(self, project):
        configure_channels(
            r={"type": "spy", "label": "r", "on": ["readout"]},
            s={"type": "spy", "label": "s", "on": ["srm"]},
            e={"type": "spy", "label": "e", "on": ["error"]},
        )

        run_notify()

        assert kinds_on("r") == ["readout", "readout"]
        assert kinds_on("s") == ["readout"]  # the payload's kind, re-classified
        assert kinds_on("e") == ["error"]

    def test_verdict_change_fires_only_on_a_flip(self, project):
        configure_channels(
            v={"type": "spy", "label": "v", "on": ["verdict_change"]},
            r={"type": "spy", "label": "r", "on": ["readout"]},
        )
        assert run_notify("--select", HEALTHY).exit_code == 0
        assert kinds_on("v") == []  # the first announcement is not a flip
        first_verdict = SpyChannel.sent[0][1].verdict

        # move the decision: recompute the whole series over inverted data
        _flip_the_horizon(project)
        refreshed = run_notify(
            "--select", HEALTHY, "--full-refresh", "--from", "2024-07-01", "--to", "2024-07-16"
        )
        assert refreshed.exit_code == 0, refreshed.output

        assert kinds_on("v") == ["readout"]
        assert SpyChannel.sent[-1][1].verdict != first_verdict

    def test_stale_fires_when_the_schedule_slipped(self, project, monkeypatch):
        configure_channels(t={"type": "spy", "label": "t", "on": ["stale"]})
        import abkit.pipeline.driver as driver_mod

        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 7, 4))
        assert runner.invoke(cli, ["run", "--select", HEALTHY]).exit_code == 0

        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
        result = run_notify("--select", HEALTHY)

        assert result.exit_code == 0, result.output
        assert kinds_on("t") == ["stale"]
        assert "behind the watermark" in SpyChannel.sent[0][1].notice

    def test_calibration_red_fires_from_validate(self, project, monkeypatch):
        configure_channels(c={"type": "spy", "label": "c", "on": ["calibration_red"]})
        assert runner.invoke(cli, ["run", "--select", HEALTHY]).exit_code == 0
        import abkit.tuning.recompute as recompute_mod

        # every measurable cell over budget: redness is the fixture's decision,
        # not a number the A/A engine is forbidden from improving
        monkeypatch.setattr(recompute_mod, "resolve_fpr_budget", lambda *a, **k: 0.0)

        result = runner.invoke(
            cli, ["validate", "--select", HEALTHY, "--iterations", "60", "--notify"]
        )

        assert result.exit_code == 0, result.output
        assert kinds_on("c") == ["calibration_red"]
        assert "false-positive budget" in SpyChannel.sent[0][1].notice

    def test_every_declared_kind_has_a_channel_scoped_to_it_here(self):
        """The roster law, DERIVED rather than restated.

        A hand-written set of covered kinds passes by being edited; this reads
        the file's own `on: [...]` channel configs, so a kind added to
        `SignalKind` with no test scoping a channel to it fails — which is the
        situation `verdict_change` sat in for four work packages.
        """
        source = Path(__file__).read_text(encoding="utf-8")
        scoped = {
            kind.strip().strip("\"'")
            for group in re.findall(r'"on": \[([^\]]*)\]', source)
            for kind in group.split(",")
            if kind.strip()
        }

        assert scoped == set(SIGNAL_KINDS)


def _flip_the_horizon(warehouse) -> None:
    """Invert the treatment lift, so the next run's verdict is the opposite one.

    Rewrites the seed mirror's answer rather than the persisted rows: the
    verdict a message carries must come from `readout.evaluate()` over rows the
    pipeline really computed, so a test that edited `_ab_results` directly
    would prove nothing about the seam.
    """
    original = warehouse.execute_query

    def inverted(query, params=None):
        rows = original(query, params)
        if isinstance(rows, list) and rows and "gross_usd" in rows[0]:
            for row in rows:
                if row.get("variant") == "treatment":
                    row["gross_usd"] = float(row["gross_usd"]) * 0.5
        if isinstance(rows, list) and rows and "signed_up" in rows[0]:
            for row in rows:
                if row.get("variant") == "treatment":
                    row["signed_up"] = 0
        return rows

    warehouse.execute_query = inverted
