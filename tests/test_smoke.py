"""Smoke tests: the package imports, is versioned, and the ``abk`` entry point runs.

Real behaviour tests (golden vs the legacy engine, A/A calibration, e2e) arrive
with the implementation — see ``docs/specs/`` and ``ROADMAP.md``.
"""

from __future__ import annotations

from click.testing import CliRunner

import abkit
from abkit.cli.main import cli


def test_version_is_nonempty_string() -> None:
    assert isinstance(abkit.__version__, str)
    assert abkit.__version__


def test_cli_version_runs() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "abk" in result.output


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
