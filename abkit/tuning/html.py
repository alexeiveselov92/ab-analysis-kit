"""Render the cockpit payloads into self-contained HTML pages (WP6, DASH-3).

The same delivery model — and the same WP3-hardened template mechanics — as
``reporting/html_report.py``: one document, the committed ``assets/*.js``
bundle inlined, the data baked as a JS literal. One-pass regex substitution
(never ``.format``, never sequential ``str.replace``), every ``<`` in the baked
JSON escaped as ``\\u003c``, no webfonts, no network (CLAUDE.md invariant 6).

Two pages, one mechanism:

* ``render_explore_html`` — the per-experiment explore cockpit. The bundle
  assigns ``window.__ABK_EXPLORE__``; the page mounts it on ``#abk-explore``.
  Built from ``web/src/explore/`` by ``web/build.mjs``.
* ``render_dashboard_html`` — the project-level dashboard
  (``docs/specs/m11-implementation-plan.md`` DASH-3), assigning
  ``window.__ABK_DASHBOARD__`` on ``#abk-dashboard``, with the SAME favicon
  and page shell as explore, so no new hex enters the CI hex-containment scan.

Both read their bundle from ``abkit/tuning/assets/`` through the same
undegrading :func:`_bundle` reader: a missing bundle RAISES. Both files are
committed and named in the CI wheel-namelist gate, so an absent one is a
packaging bug, and papering over it would serve a page that blames the reader
("run ``npm run build``") for something a ``pip install`` user cannot fix.

``dashboard.js`` reached that state in DASH-6. Between DASH-3 and DASH-5 it did
not exist — the bundle could not be committed before its sources were authored,
because a stub would have had to smuggle the three ``abk-*`` marker classes CI
greps out of every ``abkit/*/assets/*.js`` past a gate they exist to enforce
(the M3 precedent, a committed placeholder ``explore.js``, predates that gate)
— so ``render_dashboard_html`` degraded to a "the client bundle is not built"
note. DASH-5 committed the real bundle and DASH-6 added it to the namelist
tuple, which is the condition the explore reader's law was always keyed to; the
note is gone rather than kept as unreachable code with its own second contract.
"""

from __future__ import annotations

import re
from html import escape
from importlib.resources import files

from abkit.utils.json_utils import json_dumps_sorted

# The abkit "Diverge" brand mark (data-URI, no network): an Iris tile with one
# node fanning into two arms (docs/design/brand-tokens.md §Logo) — the same mark
# the report shell + test-report channels use. Its two hexes (%236a45c4 iris,
# %23fbf9f3 paper) live in the one brand-token layer (web/src/shared/chart.ts
# TOKEN_FALLBACKS), pinned by the CI hex-containment gate.
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
    "%3E%3Crect x='3' y='3' width='94' height='94' rx='26' fill='%236a45c4'/%3E"
    "%3Cg fill='none' stroke='%23fbf9f3' stroke-width='9' stroke-linecap='round'"
    " stroke-linejoin='round'%3E%3Cpolyline points='13 50 34 50'/%3E"
    "%3Cpolyline points='34 50 86 27'/%3E%3Cpolyline points='34 50 86 61'/%3E%3C/g%3E"
    "%3Ccircle cx='86' cy='27' r='7' fill='%23fbf9f3'/%3E%3C/svg%3E"
)

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>abkit explore — __EXPERIMENT__</title>
<link rel="icon" href="__FAVICON__" />
<style>
/* Page shell only — the renderer injects its own scoped styles under the
   abk-explore root. System fonts: zero network requests. */
html,body{margin:0;background:#f5f1e8;color:#1b1916;
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;}
*{box-sizing:border-box;}
</style>
</head>
<body>
<div id="abk-explore"></div>
<script>window.__ABK_EXPLORE_PAYLOAD__ = __PAYLOAD__;</script>
<script>__EXPLORE_JS__</script>
<script>
(function(){
  var mount = document.getElementById('abk-explore');
  try { window.__ABK_EXPLORE__.render(window.__ABK_EXPLORE_PAYLOAD__, mount); }
  catch (e) { mount.textContent = 'Failed to render explore: ' + e; }
})();
</script>
</body>
</html>
"""


_DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>abkit dashboard — __PROJECT__</title>
<link rel="icon" href="__FAVICON__" />
<style>
/* Page shell only — the renderer injects its own scoped styles under the
   abk-dashboard root. System fonts: zero network requests. The two hexes are
   explore's, unchanged: the CI hex-containment gate scans this file. */
html,body{margin:0;background:#f5f1e8;color:#1b1916;
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;}
*{box-sizing:border-box;}
</style>
</head>
<body>
<div id="abk-dashboard"></div>
<script>window.__ABK_DASHBOARD_PAYLOAD__ = __PAYLOAD__;</script>
<script>__DASHBOARD_JS__</script>
<script>
(function(){
  var mount = document.getElementById('abk-dashboard');
  try { window.__ABK_DASHBOARD__.render(window.__ABK_DASHBOARD_PAYLOAD__, mount); }
  catch (e) { mount.textContent = 'Failed to render dashboard: ' + e; }
})();
</script>
</body>
</html>
"""


def _bundle(name: str) -> str:
    """One committed browser bundle's text, shipped in the wheel.

    Deliberately NOT degrading (module docstring): every bundle read here is
    committed AND asserted in the CI wheel namelist, so a missing one is a
    packaging failure to surface, not to paper over.
    """
    return (files("abkit.tuning") / "assets" / name).read_text(encoding="utf-8")


def _explore_js() -> str:
    """The committed explore-cockpit renderer bundle."""
    return _bundle("explore.js")


def _dashboard_js() -> str:
    """The committed project-dashboard renderer bundle."""
    return _bundle("dashboard.js")


def _bake_payload_json(payload: dict) -> str:
    """Canonical JSON with every ``<`` escaped (the WP3-hardened bake)."""
    return json_dumps_sorted(payload).replace("<", "\\u003c")


_PLACEHOLDER_RE = re.compile(r"__(EXPERIMENT|FAVICON|PAYLOAD|EXPLORE_JS)__")
_DASHBOARD_PLACEHOLDER_RE = re.compile(r"__(PROJECT|FAVICON|PAYLOAD|DASHBOARD_JS)__")


def render_explore_html(payload: dict) -> str:
    """Build the self-contained explore HTML document for one payload.

    Pure: no DB, no filesystem writes, no clock. The server bakes it once
    post-bind; ``--no-serve`` writes it to ``reports/`` (WP8).
    """
    values = {
        "EXPERIMENT": escape(str(payload.get("experiment", "experiment"))),
        "FAVICON": _FAVICON,
        "PAYLOAD": _bake_payload_json(payload),
        "EXPLORE_JS": _explore_js(),
    }
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], _TEMPLATE)


def render_dashboard_html(payload: dict) -> str:
    """Build the self-contained dashboard HTML document for one boot payload.

    Pure, like :func:`render_explore_html`: no DB, no filesystem writes, no
    clock (``generated_at`` is stamped by the caller that builds the payload).
    The boot payload is metadata-only — every statistic arrives later over
    ``GET /api/stats/<experiment>`` — and carries no token: the client reads it
    from ``location.search``, the donor's contract, so the served page is not a
    credential at rest.
    """
    values = {
        "PROJECT": escape(str(payload.get("project", "project"))),
        "FAVICON": _FAVICON,
        "PAYLOAD": _bake_payload_json(payload),
        "DASHBOARD_JS": _dashboard_js(),
    }
    return _DASHBOARD_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], _DASHBOARD_TEMPLATE)
