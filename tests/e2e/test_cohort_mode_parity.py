"""m8 WP4 — the cross-command parity gate over the scaffolded example.

Two identical ``abk init`` projects on the same seed mirror, differing ONLY in
``assignment.cohort_copy.enabled``: `abk run`, `abk validate` and
`abk explore --no-serve` must produce identical NUMBERS (``_ab_results`` /
``_ab_aa_runs`` / the baked explore payload), while the write paths are
opposite — the no-copy default never touches ``_ab_exposures``, the opt-in
copy persists the full cohort. Volatile columns excluded from parity:
wall-clock version stamps (``created_at``, the ``run_id`` run-stamp prefix,
``generated_at``) and ``metric_rendered_query`` (direct mode embeds the
assignment subquery BY DESIGN — provenance, not a number).

The tuning ``/reload`` leg lives beside the server harness
(``tests/tuning/test_server.py::TestReload``); the backend-level load parity
is pinned in ``tests/compute/test_recompute_backend.py`` (WP3).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"

#: columns legitimately allowed to differ between the two modes
VOLATILE = {"created_at", "loaded_at", "run_id", "metric_rendered_query"}


def _scaffold(tmp_path, monkeypatch, name: str, copy_enabled: bool) -> SeedMirrorWarehouse:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", name]).exit_code == 0
    monkeypatch.chdir(tmp_path / name)
    if copy_enabled:
        yml = Path("experiments") / f"{EXP}.yml"
        text = yml.read_text(encoding="utf-8")
        anchor = "  query_file: sql/example_assignment.sql"
        assert anchor in text, "the scaffold's assignment block moved — update this test"
        yml.write_text(
            text.replace(anchor, anchor + "\n  cohort_copy: {enabled: true}", 1),
            encoding="utf-8",
        )
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


def _comparable(rows: list[dict]) -> list[dict]:
    stripped = [{k: v for k, v in r.items() if k not in VOLATILE} for r in rows]
    return sorted(stripped, key=lambda r: repr(sorted(r.items())))


def _baked_explore_payload() -> dict:
    html = (Path("reports") / f"{EXP}__explore.html").read_text(encoding="utf-8")
    marker = "window.__ABK_EXPLORE_PAYLOAD__ = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    payload = json.loads(html[start:end])
    payload.pop("generated_at", None)
    payload.pop("project", None)  # the two scaffolds differ in project NAME only
    return payload


@pytest.mark.parametrize("command", ["run", "validate", "explore"])
def test_numbers_identical_across_modes(tmp_path, monkeypatch, command):
    persisted: dict[str, list[dict]] = {}
    for mode, copy_enabled in (("direct", False), ("copy", True)):
        warehouse = _scaffold(tmp_path, monkeypatch, f"demo_{mode}", copy_enabled)
        assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
        if command == "validate":
            result = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "60"])
            assert result.exit_code == 0, result.output
            persisted[mode] = warehouse._rows.get("_ab_aa_runs", [])
        elif command == "explore":
            result = runner.invoke(cli, ["explore", "--select", EXP, "--no-serve"])
            assert result.exit_code == 0, result.output
            persisted[mode] = [_baked_explore_payload()]
        else:
            persisted[mode] = warehouse._rows.get("_ab_results", [])

        # the write-path assertion rides along on both parametrizations
        exposures = warehouse._rows.get("_ab_exposures", [])
        if copy_enabled:
            assert exposures, "copy mode must persist the cohort"
        else:
            assert exposures == [], "the no-copy default must never write _ab_exposures"

    assert persisted["direct"], f"{command} persisted no rows — the gate compared nothing"
    assert _comparable(persisted["direct"]) == _comparable(persisted["copy"])
