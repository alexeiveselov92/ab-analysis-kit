"""WP7 gates: the committed explore bundle + the client-contract payload half.

The report-side ``test_html_report.py`` pattern applied to the cockpit page:
the ``importlib.resources`` read of the real committed asset (with the §4
marker classes — the CI bundle grep's in-repo mirror), the pyproject/MANIFEST
lockstep guard, the structural three-script bake assertions with a hostile
payload, single-pass substitution, utf-8 round-trip, render purity — plus the
WP7 payload extension: the ``explore.experiment`` knob block whose baked
numbers must let the browser mirror ``analyze.effective_alphas`` exactly
(the client resolves raw alpha + correction to the effective per-comparison
alpha every ``/recompute`` sends).
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from typing import get_args

from synthetic_ab import (
    SyntheticWarehouse,
    build_session,
    make_experiment,
    seed_all_events,
    seed_cohort,
)

from abkit.config.experiment_config import CorrectionKind
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import RecomputeEngine, build_explore_payload
from abkit.tuning.html import render_explore_html

REPO_ROOT = Path(__file__).resolve().parents[2]

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}


def _payload(alpha: float | None = None) -> dict:
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse)
    seed_all_events(warehouse)
    tables = InternalTablesManager(warehouse)
    experiment = make_experiment("exp_pb", "arpu", T_TEST, alpha=alpha)
    session = build_session(warehouse, tables, experiment)
    return build_explore_payload(session, RecomputeEngine(session), {"experiment": "exp_pb"})


class TestExperimentKnobBlock:
    """payload['explore']['experiment'] — the WP7 client-mirror substrate."""

    def test_block_resolves_project_defaults(self):
        block = _payload()["explore"]["experiment"]
        assert block["alpha"] == 0.05  # project default (experiment.alpha unset)
        assert block["correction"] == "bonferroni"
        assert block["groups_count"] == 2
        assert block["non_main_count"] == 0  # the one comparison is main

    def test_experiment_alpha_override_wins(self):
        block = _payload(alpha=0.01)["explore"]["experiment"]
        assert block["alpha"] == 0.01

    def test_correction_choices_stay_in_lockstep_with_the_config_literal(self):
        # the client renders the correction seg control from this list — it
        # must be the config schema, never a hand-maintained copy
        block = _payload()["explore"]["experiment"]
        assert block["correction_choices"] == list(get_args(CorrectionKind))

    def test_baked_numbers_reproduce_the_configured_effective_alpha(self):
        """The client-mirror contract: two-tier arithmetic over the baked
        block must land exactly on the surface's configured alpha — this is
        the same formula web/src/explore/explore.ts#effectiveAlpha runs."""
        payload = _payload()
        block = payload["explore"]["experiment"]
        surface = payload["explore"]["metrics"]["arpu"]
        pairs = block["groups_count"] * (block["groups_count"] - 1) / 2
        if block["correction"] == "bonferroni":
            mirrored = block["alpha"] / pairs  # main metric, non_main == 0
        else:
            mirrored = block["alpha"]
        assert mirrored == surface["configured"]["alpha"]


class TestBundlePackaging:
    """The committed explore.js is the real cockpit, wheel-shipped."""

    def test_committed_bundle_is_the_real_client(self):
        bundle = (files("abkit.tuning") / "assets" / "explore.js").read_text(encoding="utf-8")
        assert len(bundle) > 10_000  # the WP6 placeholder stub was ~1 KB
        assert "__ABK_EXPLORE__" in bundle
        for marker in ("abk-prehorizon", "abk-insufficient", "abk-srm-fail"):
            assert marker in bundle  # §4 markers survive minification

    def test_pyproject_and_manifest_ship_the_asset(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert '"abkit.tuning" = ["assets/*.js"]' in pyproject
        manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        assert "recursive-include abkit/tuning/assets *.js" in manifest
        assert (REPO_ROOT / "abkit" / "tuning" / "assets" / "explore.js").is_file()

    def test_peeking_marker_classes_in_the_baked_document(self):
        html = render_explore_html({"experiment": "exp_pb"})
        for marker in ("abk-prehorizon", "abk-insufficient", "abk-srm-fail"):
            assert marker in html


class _ScriptCounter(HTMLParser):
    """Count <script> elements — a payload string that terminated the script
    block early would change the document structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts = 0
        self.mount_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
        if tag == "div":
            for name, value in attrs:
                if name == "id" and value:
                    self.mount_ids.append(value)


class TestScriptEscaping:
    def test_hostile_payload_keeps_the_three_script_structure(self):
        payload = {
            "experiment": "exp </script><script>alert(1)</script>",
            "note": "<!--<script> the tokenizer double-escape hazard",
        }
        html = render_explore_html(payload)
        parser = _ScriptCounter()
        parser.feed(html)
        assert parser.scripts == 3  # payload / bundle / bootstrap — exactly
        assert "abk-explore" in parser.mount_ids
        line = next(ln for ln in html.splitlines() if "window.__ABK_EXPLORE_PAYLOAD__" in ln)
        baked = line.split("window.__ABK_EXPLORE_PAYLOAD__ = ", 1)[1]
        assert baked.endswith(";</script>")
        assert "<" not in baked[: -len(";</script>")]

    def test_single_pass_substitution_payload_tokens_survive(self):
        # a payload that CONTAINS the template placeholders must ride through
        # untouched — one-pass regex substitution, never sequential replace
        payload = {"experiment": "x", "note": "__EXPLORE_JS__ and __PAYLOAD__ survive"}
        html = render_explore_html(payload)
        assert html.count("__EXPLORE_JS__") == 1
        assert html.count("__PAYLOAD__") == 1

    def test_utf8_round_trip(self):
        html = render_explore_html({"experiment": "эксперимент — тест"})
        assert "эксперимент — тест" in html
        assert html.encode("utf-8").decode("utf-8") == html

    def test_render_is_pure_over_the_payload(self):
        payload = _payload()
        snapshot = json.dumps(payload, sort_keys=True, default=str)
        render_explore_html(payload)
        assert json.dumps(payload, sort_keys=True, default=str) == snapshot
