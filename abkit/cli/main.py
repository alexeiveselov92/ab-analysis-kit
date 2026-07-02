"""``abk`` — the ab-analysis-kit command-line interface.

Pre-development: the full command surface (``init``, ``run``, ``explore``,
``validate``, ``plan``, ``init-claude``, ``clean``, ``unlock``, ``test-report``)
is specified in ``docs/specs/cli-and-dx.md`` and implemented per ``ROADMAP.md``.
This module currently exposes only the top-level group so the ``abk`` entry point
resolves and ``abk --version`` works.
"""

from __future__ import annotations

import click

from abkit import __version__


@click.group()
@click.version_option(__version__, prog_name="abk")
def cli() -> None:
    """ab-analysis-kit — declarative A/B experiment analysis.

    Commands are being built per docs/specs/cli-and-dx.md and ROADMAP.md.
    Docs: https://abkit.pipelab.dev
    """


if __name__ == "__main__":
    cli()
