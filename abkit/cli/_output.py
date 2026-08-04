"""Shared CLI output helpers so every command renders in one house style.

Mirrors the validate → plan → load → srm → compute → result pipeline's tree
look (``┌─ / │ / └─``) used by ``abk run`` so the maintenance commands
(``abk clean``, ``abk unlock``) match it instead of each inventing its own
formatting.

House conventions:
- An experiment *with* something to report is a tree: a cyan-bold ``┌─ <name>``
  header followed by one child line per item (``│   `` for all but the last,
  ``└─ `` for the last).
- An experiment with *nothing* to do is a single ``•`` line.
- A per-experiment error is a red ``✗`` line (to stderr).
- The final summary is a cyan-bold ``Done. …`` line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:  # numpy would load on `abk --help` otherwise (lazy-group rule)
    from abkit.stats import TwoTierAlphas

# Pipeline stage name → display title for the streamed run-log tree
# (architecture.md §5 stage order).
RUN_STAGE_TITLES = {
    "validate": "VALIDATE",
    "plan": "PLAN",
    "load": "LOAD",
    "srm": "SRM",
    "compute": "COMPUTE",
    "result": "RESULT",
}


def pairs_phrase(alphas: TwoTierAlphas) -> str:
    """How many variant pairs the two-tier levels were divided by, and which.

    ``abk run`` and ``abk validate`` print this divisor, and both printed the
    literal ``C(g,2)`` — true only of the default family. (``abk plan`` states
    its levels differently; its own note is ``plan._correction_note``.) Under
    ``contrasts: vs_control`` (m13 STAT-1b) the count is ``g−1`` and the line
    has to SAY so: a divisor an operator cannot reconcile with the alpha beside
    it reads as a bug in whichever of the two they trust less. Reads the
    resolved object rather than re-deriving from ``groups_count``, which no
    longer determines the count.

    Unlike ``_correction_note`` it stays loud at two arms and under
    ``correction: none``: this line reports what the family IS, not why a level
    moved, and "1 contrast against the control arm" is true in both cases.
    """
    g = alphas.groups_count
    # integral for every integer g >= 2 in both families (g(g−1) is even)
    shown = int(alphas.pairs_count)
    if alphas.contrasts == "vs_control":
        return f"contrasts: vs_control ⇒ {shown} contrast(s) against the control arm"
    return f"C({g},2)={shown} pairs"


def echo_block(
    title: str,
    children: list[str],
    *,
    warnings: list[str] | None = None,
    echo: Callable[[str], None] = click.echo,
) -> None:
    """Print a cyan-bold ``┌─ title`` header with ``│``/``└─`` child lines.

    The injectable core of the house tree style: ``warnings`` render as yellow
    ``│`` continuation lines above the children, the last child gets the
    ``└─`` elbow. ``echo`` defaults to ``click.echo`` but can be any line sink
    (the explore server reuses these blocks in M3). ``children`` must be
    non-empty.
    """
    echo(click.style(f"  ┌─ {title}", fg="cyan", bold=True))
    for warning in warnings or []:
        echo(click.style(f"  │   ⚠ {warning}", fg="yellow", bold=True))
    last = len(children) - 1
    for i, child in enumerate(children):
        prefix = "  └─ " if i == last else "  │   "
        echo(f"{prefix}{child}")


def echo_tree(name: str, children: list[str], *, warnings: list[str] | None = None) -> None:
    """Print a ``┌─ name`` header with ``│``/``└─`` child lines."""
    echo_block(name, children, warnings=warnings)


class StageLogRenderer:
    """Stream ``(stage, line)`` pipeline progress as the run-log tree.

    Opens a cyan-bold ``┌─ TITLE`` header the first time each stage appears and
    prints every subsequent line for that stage as a ``│   `` child. ``titles``
    maps a stage name to its display title (unmapped names fall back to
    ``upper()``); ``echo`` is injectable so one renderer drives both the CLI
    and the M3 explore server. Build a fresh renderer per run so the first
    stage re-opens its header.
    """

    def __init__(
        self,
        *,
        titles: dict[str, str] | None = None,
        echo: Callable[[str], None] = click.echo,
    ) -> None:
        self._open: str | None = None
        self._titles = titles or RUN_STAGE_TITLES
        self._echo = echo

    def __call__(self, stage: str, line: str) -> None:
        if self._open != stage:
            title = self._titles.get(stage, stage.upper())
            self._echo(click.style(f"  ┌─ {title}", fg="cyan", bold=True))
            self._open = stage
        self._echo(f"  │   {line}")


def echo_noop(name: str, reason: str) -> None:
    """An experiment with nothing to do — a single ``•`` line."""
    click.echo(f"  • {name}: {reason}")


def echo_error(name: str, message: str) -> None:
    """A per-experiment failure — a red ``✗`` line on stderr."""
    click.echo(click.style(f"  ✗ {name}: {message}", fg="red"), err=True)


def echo_srm(message: str) -> None:
    """The loud red SRM gate line (data-contract-and-reporting.md §6)."""
    click.echo(click.style(f"  ✗ {message}", fg="red", bold=True), err=True)


def echo_done(summary: str) -> None:
    """The closing ``Done. …`` summary (cyan, bold), preceded by a blank line."""
    click.echo()
    click.echo(click.style(f"Done. {summary}", fg="cyan", bold=True))
