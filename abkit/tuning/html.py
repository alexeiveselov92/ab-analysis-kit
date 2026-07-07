"""Render the explore payload into one self-contained HTML page (WP6).

The same delivery model — and the same WP3-hardened template mechanics — as
``reporting/html_report.py``: one document, the committed ``assets/explore.js``
bundle inlined, the data baked as a JS literal. One-pass regex substitution
(never ``.format``, never sequential ``str.replace``), every ``<`` in the baked
JSON escaped as ``\\u003c``, no webfonts, no network (CLAUDE.md invariant 6).

The bundle assigns ``window.__ABK_EXPLORE__``; the page mounts it on
``#abk-explore``. The committed bundle is built from ``web/src/explore/`` by
``web/build.mjs`` (WP7 — until it lands, a minimal placeholder bundle renders
an honest pending note).
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


def _explore_js() -> str:
    """Read the committed explore renderer bundle shipped in the wheel."""
    return (files("abkit.tuning") / "assets" / "explore.js").read_text(encoding="utf-8")


def _bake_payload_json(payload: dict) -> str:
    """Canonical JSON with every ``<`` escaped (the WP3-hardened bake)."""
    return json_dumps_sorted(payload).replace("<", "\\u003c")


_PLACEHOLDER_RE = re.compile(r"__(EXPERIMENT|FAVICON|PAYLOAD|EXPLORE_JS)__")


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
