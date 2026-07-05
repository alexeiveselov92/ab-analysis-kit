"""A self-contained HTML matrix report for ``abk validate --report`` (m4 WP4).

WP4 ships a framework-free, no-external-reference HTML table (the hardened bake:
every ``<`` in dynamic text escaped, no CDN/webfont/script src). WP5 upgrades this
to the committed report bundle with the peeking-FPR-vs-looks curve and the shared
brand-token layer; the ``render_validate_report`` signature stays stable.
"""

from __future__ import annotations

from html import escape

from abkit.validate.result import AaValidateResult

_GOOD = "#1a7f37"  # in-budget
_BAD = "#cf222e"  # over budget / do-not-use
_MUTED = "#57606a"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _row(cell) -> str:
    over = cell.fpr is not None and cell.budget is not None and cell.fpr > cell.budget
    fpr_color = _BAD if over else _GOOD if cell.fpr is not None else _MUTED
    star = " ★" if cell.recommended else ""
    cells = [
        escape(f"{cell.method_name}{star}"),
        f'<span style="color:{fpr_color}">{_pct(cell.fpr)}</span>',
        _pct(cell.peeking_fpr),
        _pct(cell.power),
        "—" if cell.achieved_mde is None else escape(f"{cell.achieved_mde:.4g}"),
        _pct(cell.coverage),
        escape(f"{cell.alpha:g}"),
        escape(cell.verdict),
    ]
    weight = "600" if cell.recommended else "400"
    return f'<tr style="font-weight:{weight}"><td>' + "</td><td>".join(cells) + "</td></tr>"


def render_validate_report(result: AaValidateResult, *, generated_at: str = "") -> str:
    """Render the A/A matrix as one self-contained HTML page (no external refs)."""
    header = (
        "<tr><th>method</th><th>FPR</th><th>peeking FPR</th><th>power</th>"
        "<th>achieved MDE</th><th>coverage</th><th>α</th><th>verdict</th></tr>"
    )
    by_metric: dict[str, list] = {}
    for cell in result.cells:
        by_metric.setdefault(cell.metric, []).append(cell)

    sections = []
    for metric, cells in sorted(by_metric.items()):
        rows = "".join(_row(c) for c in cells)
        sections.append(f"<h2>{escape(metric)}</h2><table>{header}{rows}</table>")
    body = "".join(sections) or "<p>No cells scored.</p>"
    stamp = escape(generated_at) if generated_at else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>A/A validation — {escape(result.experiment)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:2rem;color:#1f2328}"
        "table{border-collapse:collapse;margin:0 0 2rem;width:100%}"
        "th,td{border:1px solid #d0d7de;padding:.4rem .6rem;text-align:left;font-size:.9rem}"
        "th{background:#f6f8fa}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.5rem}"
        f".muted{{color:{_MUTED}}}"
        "</style></head><body>"
        f"<h1>A/A false-positive matrix — {escape(result.experiment)}</h1>"
        f"<p class='muted'>★ = recommended · {stamp}</p>"
        f"{body}</body></html>"
    )
