"""Render an experiment report payload into one self-contained HTML file.

The self-contained inline-bundle delivery model (the detectkit donor pattern,
shared with the M3 explore page): one HTML document with the renderer JS
inlined (the pre-built, committed ``assets/report.js`` bundle — built from
``web/src/report/`` by ``web/build.mjs``) and the data baked in as a JS
literal. No CDN, no webfonts, no network — nothing leaves the browser, and the
document embeds in a future app unchanged (CLAUDE.md invariant 6).

Template mechanics (m3-implementation-plan.md WP3 hotspots, hardened past the
donor per the WP3 adversarial review):

- Placeholders are substituted in ONE regex pass (never ``.format`` — literal
  ``{}`` in the JS/CSS survive; never sequential ``str.replace`` — a payload
  string containing a placeholder token like ``__REPORT_JS__`` must not be
  re-scanned and clobbered).
- The baked payload JSON escapes EVERY ``<`` as ``\\u003c`` (valid JSON, same
  parsed string). Escaping only ``</`` — the donor's hardening — is not
  enough: per the HTML tokenizer a ``<!--`` + ``<script`` pair inside script
  data enters the double-escaped state and swallows the real ``</script>``
  terminator, so no ``<`` may survive at all (data-contract §5.3 bake-time
  escaping).
"""

from __future__ import annotations

import re
from html import escape
from importlib.resources import files

from abkit.utils.json_utils import json_dumps_sorted

# A small placeholder-brand tile (data-URI, no network): an A/B split square
# using the placeholder accent from the one brand-token layer — swapped with
# the finalized logo/palette per branding-and-site.md.
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' rx='7' fill='%233987e5'/%3E%3Crect x='17' y='6'"
    " width='9' height='20' rx='2' fill='%23fcfcfb' opacity='0.55'/%3E%3Crect x='6' "
    "y='12' width='9' height='14' rx='2' fill='%23fcfcfb'/%3E%3C/svg%3E"
)

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>abkit report — __EXPERIMENT__</title>
<link rel="icon" href="__FAVICON__" />
<style>
/* Page shell only — the renderer injects its own scoped styles and the one
   brand-token layer (web/src/report/report.ts injectStyle). System fonts:
   the report must open file:// with zero network requests. */
html,body{margin:0;background:#f9f9f7;color:#0b0b0b;
  font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;}
*{box-sizing:border-box;}
</style>
</head>
<body>
<div id="abk-report"></div>
<script>window.__ABK_PAYLOAD__ = __PAYLOAD__;</script>
<script>__REPORT_JS__</script>
<script>
(function(){
  var mount = document.getElementById('abk-report');
  try { window.__ABK_REPORT__.render(window.__ABK_PAYLOAD__, mount); }
  catch (e) { mount.textContent = 'Failed to render report: ' + e; }
})();
</script>
</body>
</html>
"""


def _report_js() -> str:
    """Read the committed report renderer bundle shipped in the wheel."""
    return (files("abkit.reporting") / "assets" / "report.js").read_text(encoding="utf-8")


def _bake_payload_json(payload: dict) -> str:
    """Canonical JSON with every ``<`` escaped as ``\\u003c`` (script-safe).

    ``\\u003c`` is a plain JSON escape — the parsed value is byte-identical —
    but no ``<`` survives in the document, so payload strings can neither
    terminate the inline script block (``</script>``) nor poison the HTML
    tokenizer's script-data-escaped states (``<!--`` + ``<script``, which
    would swallow the real terminator even with ``</`` escaped).
    """
    return json_dumps_sorted(payload).replace("<", "\\u003c")


_PLACEHOLDER_RE = re.compile(r"__(EXPERIMENT|FAVICON|PAYLOAD|REPORT_JS)__")


def render_report_html(payload: dict) -> str:
    """Build the self-contained HTML document for one experiment ``payload``.

    Pure: no DB, no filesystem writes, no clock. The caller writes the
    returned string (``abk run --report`` — cli/commands/run.py).
    """
    values = {
        "EXPERIMENT": escape(str(payload.get("experiment", "experiment"))),
        "FAVICON": _FAVICON,
        "PAYLOAD": _bake_payload_json(payload),
        "REPORT_JS": _report_js(),
    }
    # One pass: substituted content (payload strings, the bundle body) is
    # never re-scanned for other placeholder tokens.
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], _TEMPLATE)
