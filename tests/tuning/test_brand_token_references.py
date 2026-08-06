"""Every ``var(--abk-*)`` a bundle emits must resolve (m14 DEC-4 review).

The existing CI gates check the brand DEFINITIONS (brand.css ↔ ``TOKEN_FALLBACKS``)
and hunt raw hexes in the Python page shells. Neither validates a **reference**,
so an invented token name reaches the browser silently — and CSS's
"invalid at computed-value time" makes the failure worse than a wrong colour: in
a SHORTHAND (``border: 1px solid var(--nope)``) every longhand resets to its
initial value, so the element loses its border entirely rather than falling back
to a default one.

Two such tokens shipped in one WP before this gate existed (``--abk-accent`` on
the dashboard, ``--abk-line``/``--abk-ink-1`` in explore), and the second cost
the ``arm vs arm`` pill its box — the one affordance that stops a
treatment-vs-treatment ``WIN`` reading as a ship recommendation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"
CHART_TS = WEB_SRC / "shared" / "chart.ts"

#: Declared by the page shells rather than the brand token layer.
SHELL_TOKENS = frozenset({"--abk-sans", "--abk-mono"})


def _declared() -> frozenset[str]:
    """The token layer: the keys of ``TOKEN_FALLBACKS``."""
    return frozenset(re.findall(r"'(--abk-[a-z0-9-]+)':", CHART_TS.read_text(encoding="utf-8")))


def _sources() -> list[Path]:
    return sorted(WEB_SRC.rglob("*.ts"))


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_every_token_reference_resolves(source: Path) -> None:
    text = source.read_text(encoding="utf-8")
    # a file may also define tokens locally (the page shells do)
    local = frozenset(re.findall(r"(--abk-[a-z0-9-]+)\s*:", text))
    known = _declared() | SHELL_TOKENS | local
    used = frozenset(re.findall(r"var\((--abk-[a-z0-9-]+)", text))

    unresolved = sorted(used - known)
    assert not unresolved, (
        f"{source.name} references CSS custom properties nothing defines: {unresolved}. "
        "In a shorthand this silently drops the whole property."
    )


def test_the_gate_bites_on_an_invented_token(tmp_path: Path) -> None:
    """A gate that forbids something must be proven to fire."""
    hostile = tmp_path / "hostile.ts"
    hostile.write_text("const css = `.x{border:1px solid var(--abk-line);}`;", encoding="utf-8")

    with pytest.raises(AssertionError, match="--abk-line"):
        test_every_token_reference_resolves(hostile)
