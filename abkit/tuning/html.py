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

# The A/B split tile with the explore accent (data-URI, no network) — the one
# placeholder-brand token layer, swapped per branding-and-site.md.
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' rx='7' fill='%232e7d5b'/%3E%3Crect x='17' y='6'"
    " width='9' height='20' rx='2' fill='%23fcfcfb' opacity='0.55'/%3E%3Crect x='6' "
    "y='12' width='9' height='14' rx='2' fill='%23fcfcfb'/%3E%3C/svg%3E"
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
html,body{margin:0;background:#f9f9f7;color:#0b0b0b;
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
