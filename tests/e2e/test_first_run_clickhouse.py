"""The M2 DoD gate against a REAL ClickHouse (testcontainers).

Runs the exact first-run path a fresh user takes: ``abk init`` → load the
scaffolded seed SQL → ``abk run --select example_signup_test`` → real rows in
``abkit_internal._ab_results`` → idempotent re-run. Skipped when Docker or
the integration extras (``pip install -e ".[integration,clickhouse]"``) are
unavailable — CI runs it with Docker.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

testcontainers_clickhouse = pytest.importorskip(
    "testcontainers.clickhouse", reason="integration extras not installed"
)
pytest.importorskip("clickhouse_driver", reason="clickhouse-driver not installed")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker not available")

runner = CliRunner()


def _iter_seed_statements(seed: str):
    """Yield each executable statement from the seed SQL.

    Strips full-line ``--`` comments FIRST, then splits on ``;``. Order matters:
    the previous naive ``split(';')``-then-``startswith('--')`` skip was doubly
    broken — (a) the file-header comment shares a chunk with ``CREATE DATABASE
    IF NOT EXISTS analytics``, so that whole chunk (and the DB creation) was
    discarded, and (b) a comment line itself contains a ``;`` ("…their k % 14-th
    day); treatment also spends ~15% more."), so splitting first tore the
    comment across two chunks and leaked prose into the next statement. Removing
    comment lines before the split fixes both. Real users load the file via
    ``clickhouse-client --multiquery`` (comment-aware); only this Python loader
    needed the fix.
    """
    no_comments = "\n".join(line for line in seed.splitlines() if not line.strip().startswith("--"))
    for chunk in no_comments.split(";"):
        body = chunk.strip()
        if body:
            yield body


@pytest.fixture(scope="module")
def clickhouse():
    with testcontainers_clickhouse.ClickHouseContainer("clickhouse/clickhouse-server:24.3") as ch:
        yield ch


def test_first_run_against_real_clickhouse(clickhouse, tmp_path, monkeypatch):
    from clickhouse_driver import Client

    host = clickhouse.get_container_host_ip()
    port = int(clickhouse.get_exposed_port(9000))
    user = clickhouse.username
    password = clickhouse.password

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli_group(), ["init", "demo"]).exit_code == 0
    project = tmp_path / "demo"
    monkeypatch.chdir(project)

    # point the dev profile at the container
    profiles = (project / "profiles.yml").read_text()
    profiles = profiles.replace("host: localhost", f"host: {host}")
    profiles = profiles.replace("port: 9000", f"port: {port}")
    profiles = profiles.replace("user: default", f"user: {user}")
    profiles = profiles.replace('password: ""', f'password: "{password}"')
    (project / "profiles.yml").write_text(profiles)

    # load the scaffolded seed dataset, statement by statement (comments stripped
    # before the ';' split so CREATE DATABASE — which shares a chunk with the
    # header comment — is not skipped)
    client = Client(host=host, port=port, user=user, password=password)
    seed = (project / "seed" / "seed_dataset.clickhouse.sql").read_text()
    for statement in _iter_seed_statements(seed):
        client.execute(statement)

    # freeze "now" past the horizon so the whole grid is complete
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))

    result = runner.invoke(cli_group(), ["run", "--select", "example_signup_test"])
    assert result.exit_code == 0, result.output

    rows = client.execute(
        "SELECT metric, count() FROM abkit_internal._ab_results FINAL "
        "WHERE experiment = 'example_signup_test' GROUP BY metric"
    )
    assert dict(rows) == {"example_signup_cr": 14, "example_arpu": 14}

    rerun = runner.invoke(cli_group(), ["run", "--select", "example_signup_test"])
    assert rerun.exit_code == 0
    assert "cutoffs planned: 0" in rerun.output


def cli_group():
    from abkit.cli.main import cli

    return cli


_ = Path  # imported for parity with the sibling module's helpers
