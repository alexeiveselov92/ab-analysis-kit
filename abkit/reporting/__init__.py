"""Reporting: the experiment-primary baked payload (+ the WP3 HTML readout).

The payload contract is documented in data-contract-and-reporting.md §5.3 and
kept in lockstep with the renderer-side ``web/src/shared/payload.ts`` (WP3).
"""

from abkit.reporting.builder import (
    PAYLOAD_VERSION,
    REPORT_POINT_BUDGET,
    build_report_payload,
)
from abkit.reporting.html_report import render_report_html

__all__ = [
    "PAYLOAD_VERSION",
    "REPORT_POINT_BUDGET",
    "build_report_payload",
    "render_report_html",
]
