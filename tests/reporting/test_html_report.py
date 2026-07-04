"""Self-containment gate for the WP3 HTML readout (m3-implementation-plan.md WP3).

Ports the donor's ``test_report.py`` render cases to the experiment-primary
payload: placeholder consumption, the all-``<`` bake escaping (the donor's
``</`` hole plus the tokenizer's ``<!--``+``<script`` double-escape hazard),
utf-8 round-trip, zero-network self-containment, and the
implicit bundle-packaging gate (the ``importlib.resources`` read of the real
committed asset — the jinja-template precedent) plus a pyproject/MANIFEST
lockstep guard so a pip-installed wheel cannot silently lose the asset.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

from abkit.reporting import build_report_payload, render_report_html
from abkit.utils.json_utils import json_loads
from tests.reporting.test_builder import make_experiment, seed_series

REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload(tables) -> dict:
    experiment = make_experiment()
    seed_series(tables, experiment, days=14)
    return build_report_payload(experiment, tables, generated_at="2026-01-15 12:00 UTC")


def _baked_json(html: str) -> str:
    """Extract the baked payload JSON from the ``window.__ABK_PAYLOAD__`` line."""
    line = next(line for line in html.splitlines() if "window.__ABK_PAYLOAD__" in line)
    baked = line.split("window.__ABK_PAYLOAD__ = ", 1)[1]
    suffix = ";</script>"
    assert baked.endswith(suffix)
    return baked[: -len(suffix)]


class _ScriptCounter(HTMLParser):
    """Parse the document and count <script> elements — a payload string that
    terminated the script block early would change the structure."""

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


class TestSelfContainment:
    def test_no_surviving_placeholder(self, tables):
        html = render_report_html(_payload(tables))
        for placeholder in ("__PAYLOAD__", "__REPORT_JS__", "__EXPERIMENT__", "__FAVICON__"):
            assert placeholder not in html
        # the bundle global + the baked-payload global + the mount are present
        assert "__ABK_REPORT__" in html
        assert "window.__ABK_PAYLOAD__" in html
        assert 'id="abk-report"' in html

    def test_title_uses_the_experiment_name_escaped(self):
        # config validation forbids such names, but the renderer must not
        # trust its input either (render is pure over any payload dict)
        html = render_report_html({"experiment": 'exp <b>&"</b>'})
        assert "<title>abkit report — exp &lt;b&gt;&amp;&quot;&lt;/b&gt;</title>" in html

    def test_zero_network_requests(self, tables):
        """The DoD self-containment half: no webfont links, no external srcs
        (the donor's Google-Fonts <link>s are deliberately dropped)."""
        html = render_report_html(_payload(tables))
        assert "fonts.googleapis" not in html
        assert "preconnect" not in html
        assert re.search(r'\bsrc\s*=\s*["\']https?://', html) is None
        assert re.search(r'<link[^>]+href\s*=\s*["\']https?://', html) is None
        assert "@import" not in html

    def test_peeking_marker_classes_in_document(self, tables):
        """The §4 machine-checkable markers ship inside the inlined bundle."""
        html = render_report_html(_payload(tables))
        for marker in ("abk-prehorizon", "abk-insufficient", "abk-srm-fail"):
            assert marker in html

    def test_utf8_round_trip(self, tables):
        experiment = make_experiment(name="эксперимент_signup", description="описание — тест")
        seed_series(tables, experiment, days=1, metric="revenue")
        html = render_report_html(build_report_payload(experiment, tables))
        assert "эксперимент_signup" in html
        assert html.encode("utf-8").decode("utf-8") == html


class TestScriptEscaping:
    def test_payload_with_script_terminator_stays_parseable(self, tables):
        experiment = make_experiment(description='</script><script>alert("pwned")</script>')
        seed_series(tables, experiment, days=1)
        html = render_report_html(build_report_payload(experiment, tables))

        # NO `<` survives in the baked JSON — neither `</script>` nor the
        # tokenizer's `<!--`/`<script` double-escape hazard can fire
        baked = _baked_json(html)
        assert "<" not in baked
        assert "\\u003c/script>" in baked

        parser = _ScriptCounter()
        parser.feed(html)
        # exactly the template's three script blocks — the hostile string did
        # not terminate the payload block early
        assert parser.scripts == 3
        assert "abk-report" in parser.mount_ids

    def test_comment_open_plus_script_does_not_double_escape(self, tables):
        """`<!--` + `<script` inside script data enters the HTML tokenizer's
        double-escaped state and swallows the real `</script>` terminator —
        the hole that `</`-only escaping (the donor hardening) leaves open
        (WP3 adversarial-review blocker). All `<` must be baked away."""
        experiment = make_experiment(description="<!--<script>")
        seed_series(tables, experiment, days=1)
        html = render_report_html(build_report_payload(experiment, tables))
        assert "<" not in _baked_json(html)
        assert "<!--" not in html.split("window.__ABK_PAYLOAD__", 1)[1]

    def test_placeholder_token_in_payload_survives(self, tables):
        """Single-pass substitution: payload content containing a template
        placeholder token must not be re-scanned and clobbered (WP3
        adversarial-review major)."""
        experiment = make_experiment(description="docs say __REPORT_JS__ and __PAYLOAD__")
        seed_series(tables, experiment, days=1)
        html = render_report_html(build_report_payload(experiment, tables))
        parsed = json_loads(_baked_json(html))
        assert parsed["description"] == "docs say __REPORT_JS__ and __PAYLOAD__"
        # the payload's literal tokens survive untouched (a sequential-replace
        # bake would have clobbered them with the bundle/payload bodies) and
        # the template slots themselves were all consumed
        assert html.count("__REPORT_JS__") == 1
        assert html.count("__PAYLOAD__") == 1

    def test_escaped_payload_json_round_trips(self, tables):
        experiment = make_experiment(description="a </script> b <!--<script c \\u003c d")
        seed_series(tables, experiment, days=1)
        html = render_report_html(build_report_payload(experiment, tables))
        baked = _baked_json(html)
        parsed = json_loads(baked)
        # \\u003c is a plain JSON escape — the parsed value is byte-identical
        assert parsed["description"] == "a </script> b <!--<script c \\u003c d"
        # and it is standard JSON (json.loads agrees)
        assert json.loads(baked) == parsed


class TestBundlePackaging:
    def test_committed_bundle_is_readable_and_marked(self):
        """The importlib.resources read the renderer relies on (the implicit
        packaging gate — the donor stance) sees the real committed asset."""
        bundle = (files("abkit.reporting") / "assets" / "report.js").read_text(encoding="utf-8")
        assert len(bundle) > 1000
        assert "__ABK_REPORT__" in bundle
        for marker in ("abk-prehorizon", "abk-insufficient", "abk-srm-fail"):
            assert marker in bundle

    def test_wheel_packaging_declarations_lockstep(self):
        """pyproject package-data and MANIFEST.in both ship the asset — the
        tests read from the source tree, so only these lines protect a
        pip-installed wheel (donor gotcha)."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        assert '"abkit.reporting" = ["assets/*.js"]' in pyproject
        assert "recursive-include abkit/reporting/assets *.js" in manifest
        assert (REPO_ROOT / "abkit" / "reporting" / "assets" / "report.js").is_file()


class TestRenderIsPure:
    def test_render_does_not_mutate_the_payload(self, tables):
        payload = _payload(tables)
        snapshot = json.dumps(payload, sort_keys=True, default=str)
        render_report_html(payload)
        assert json.dumps(payload, sort_keys=True, default=str) == snapshot
