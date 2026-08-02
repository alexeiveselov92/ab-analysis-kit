"""The M2 DoD gate against a REAL ClickHouse (testcontainers).

Runs the exact first-run path a fresh user takes: ``abk init`` → load the
scaffolded seed SQL → ``abk run --select example_signup_test`` → real rows in
``abkit_internal._ab_results`` → idempotent re-run. Skipped when Docker or
the integration extras (``pip install -e ".[integration,clickhouse]"``) are
unavailable — CI runs it with Docker.

M9 WP6 adds the second leg, which needs a REAL backend to mean anything: an
**existing pre-M9 install** (an ``_ab_results`` table created without the four
CUPED covariate-moment columns) is migrated in place by ``ensure_columns``'
``ALTER TABLE … ADD COLUMN`` on the real dialect, then the opt-in additive
read path runs against real SQL — day state written by a real
``GROUP BY toDate(...)`` render, summed by a real ``SUM(...) GROUP BY
unit_id`` — and ``abk verify-incremental`` reconciles the whole series. The
in-memory suites can only prove the Python; the additivity of the SQL itself
is a claim about the warehouse.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

testcontainers_clickhouse = pytest.importorskip(
    "testcontainers.clickhouse", reason="integration extras not installed"
)
pytest.importorskip("clickhouse_driver", reason="clickhouse-driver not installed")

from tests._helpers.scaffold import set_incremental_reads  # noqa: E402


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


def _prepare_project(clickhouse, tmp_path, monkeypatch, name: str):
    """``abk init`` + a profile pointed at the container + a freshly loaded seed.

    The container fixture is module-scoped, so each test gets its own project
    directory AND drops both databases first — a second seed load into a
    surviving table would double every fact row.
    """
    from clickhouse_driver import Client

    host = clickhouse.get_container_host_ip()
    port = int(clickhouse.get_exposed_port(9000))
    user = clickhouse.username
    password = clickhouse.password

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli_group(), ["init", name]).exit_code == 0
    project = tmp_path / name
    monkeypatch.chdir(project)
    # PERF-1 flipped the scaffold to `incremental_reads: true`. This is the
    # ONLY real-SQL first-run gate, so it is pinned OFF to keep certifying
    # recompute against a real warehouse; the M9 migration leg below turns it
    # back on explicitly for the additive path.
    set_incremental_reads(project / "abkit_project.yml", False)

    # point the dev profile at the container
    profiles = (project / "profiles.yml").read_text()
    profiles = profiles.replace("host: localhost", f"host: {host}")
    profiles = profiles.replace("port: 9000", f"port: {port}")
    profiles = profiles.replace("user: default", f"user: {user}")
    profiles = profiles.replace('password: ""', f'password: "{password}"')
    (project / "profiles.yml").write_text(profiles)

    client = Client(host=host, port=port, user=user, password=password)
    client.execute("DROP DATABASE IF EXISTS abkit_internal")
    client.execute("DROP DATABASE IF EXISTS analytics")

    # load the scaffolded seed dataset, statement by statement (comments stripped
    # before the ';' split so CREATE DATABASE — which shares a chunk with the
    # header comment — is not skipped)
    seed = (project / "seed" / "seed_dataset.clickhouse.sql").read_text()
    for statement in _iter_seed_statements(seed):
        client.execute(statement)

    # freeze "now" past the horizon so the whole grid is complete
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return project, client, {"host": host, "port": port, "user": user, "password": password}


def test_first_run_against_real_clickhouse(clickhouse, tmp_path, monkeypatch):
    _, client, _ = _prepare_project(clickhouse, tmp_path, monkeypatch, "demo")

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


#: the four columns M9 WP1 added to `_ab_results` (the migration under test)
M9_RESULT_COLUMNS = ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2")


def _live_columns(client, table: str) -> set[str]:
    rows = client.execute(
        "SELECT name FROM system.columns WHERE database = 'abkit_internal' AND table = %(t)s",
        {"t": table},
    )
    return {row[0] for row in rows}


def test_pre_m9_install_migrates_and_the_additive_path_reconciles(
    clickhouse, tmp_path, monkeypatch
):
    """M9 WP6 (§7) on a real backend: migrate an existing pre-M9 table, then
    run the opt-in additive read path and reconcile it against recompute.

    Both halves need real SQL to be worth anything: ``ALTER TABLE … ADD
    COLUMN`` is dialect code the in-memory fake only simulates, and per-day
    additivity is a property of the metric's *rendered SQL*, not of Python.
    """
    from abkit.core.models import TableModel
    from abkit.database.clickhouse_manager import ClickHouseDatabaseManager
    from abkit.database.tables import get_results_table_model

    project, client, conn = _prepare_project(clickhouse, tmp_path, monkeypatch, "demo_m9")

    # ── an existing PRE-M9 install: `_ab_results` without the covariate moments
    current = get_results_table_model()
    pre_m9 = TableModel(
        columns=[c for c in current.columns if c.name not in M9_RESULT_COLUMNS],
        primary_key=current.primary_key,
        engine=current.engine,
        order_by=current.order_by,
        indexes=current.indexes,
        version_column=current.version_column,
    )
    manager = ClickHouseDatabaseManager(
        **conn, internal_database="abkit_internal", data_database="analytics"
    )
    try:
        manager.create_table("abkit_internal._ab_results", pre_m9)
    finally:
        manager.close()
    before = _live_columns(client, "_ab_results")
    assert before, "the pre-M9 fixture table was not created"
    assert not (set(M9_RESULT_COLUMNS) & before), "fixture must start WITHOUT the M9 columns"

    # ── turn the opt-in additive read path on and run
    set_incremental_reads(project / "abkit_project.yml", True)
    result = runner.invoke(cli_group(), ["run", "--select", "example_signup_test"])
    assert result.exit_code == 0, result.output

    # the migration ran in place — no recreate, no data loss, and the new
    # columns are populated by the very run that added them
    assert set(M9_RESULT_COLUMNS) <= _live_columns(client, "_ab_results")
    counts = client.execute(
        "SELECT metric, count() FROM abkit_internal._ab_results FINAL "
        "WHERE experiment = 'example_signup_test' GROUP BY metric"
    )
    assert dict(counts) == {"example_signup_cr": 14, "example_arpu": 14}
    moments = client.execute(
        "SELECT count() FROM abkit_internal._ab_results FINAL "
        "WHERE metric = 'example_arpu' AND cov_std_1 IS NOT NULL AND corr_coef_1 IS NOT NULL"
    )
    assert moments[0][0] == 14

    # ── the STATE stage really materialized day rows through real SQL, for the
    # declared-additive metric only
    state = client.execute(
        "SELECT source_table, count(DISTINCT day) FROM abkit_internal._ab_unit_state FINAL "
        "GROUP BY source_table"
    )
    assert dict(state) == {"example_signup_test/example_arpu": 14}

    # ── and both backends agree over the WHOLE series on a real warehouse
    verify = runner.invoke(cli_group(), ["verify-incremental", "--select", "example_signup_test"])
    assert verify.exit_code == 0, verify.output
    assert "matched at rel_tol=1e-09" in verify.output
    assert "unverified:" not in verify.output  # every cutoff really came from state
    assert "DIVERGED" not in verify.output


def _naive_utc(value: datetime) -> datetime:
    """The driver may hand back a tz-AWARE datetime for ``DateTime64(3, 'UTC')``
    (the column carries its zone) or a naive one; abkit stores naive UTC, so
    normalize instead of assuming which. A bare ``.replace(tzinfo=None)`` would
    be wrong for any aware value not already in UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _live_column_types(client, table: str) -> dict[str, str]:
    rows = client.execute(
        "SELECT name, type FROM system.columns "
        "WHERE database = 'abkit_internal' AND table = %(t)s",
        {"t": table},
    )
    return dict(rows)


def test_m10_window_schema_on_a_real_server(clickhouse, tmp_path, monkeypatch):
    """The M10 exit gate's leg 3 (§3(3)), the half only real DDL can settle.

    The in-memory fake stores column NAMES only, so "widened to
    ``DateTime64(3)``" is unprovable there — and a create-time type is exactly
    what a breaking schema change gets wrong. Everything else about the two
    breaks (names present/absent, the drop-and-recreate remedy on the terminal,
    no shipped text naming the dropped columns) is pinned without Docker in
    ``test_sub_day_anchors_and_explore.py`` and ``tests/docs``.
    """
    _, client, _ = _prepare_project(clickhouse, tmp_path, monkeypatch, "demo_m10")

    result = runner.invoke(cli_group(), ["run", "--select", "example_signup_test"])
    assert result.exit_code == 0, result.output

    # ── WP3: the two derived Date columns are gone from the results table…
    results = _live_column_types(client, "_ab_results")
    assert results, "the results table was not created"
    assert "start_date" not in results
    assert "end_date" not in results
    # …and the instants that replace them are what BI groups by
    assert results["start_ts"].startswith("DateTime64(3")
    assert results["end_ts"].startswith("DateTime64(3")

    # ── WP1/WP2: the catalog window is renamed AND widened, with the anchor
    catalog = _live_column_types(client, "_ab_experiments")
    assert "start_date" not in catalog
    assert "end_date" not in catalog
    assert catalog["start_ts"].startswith("DateTime64(3"), catalog["start_ts"]
    assert catalog["horizon_ts"].startswith("DateTime64(3"), catalog["horizon_ts"]
    assert catalog["interval_anchor"] == "String"

    # the resolved window really round-trips through the widened columns (the
    # scaffold's UTC midnight edges, stored in naive UTC like `_ab_results`)
    # No FINAL: `_ab_experiments` is a plain MergeTree (no version column), so
    # FINAL is illegal on it — abkit's own reader (`get_experiment`) does not use
    # it either, because `upsert_experiment` replaces the row with a SYNCHRONOUS
    # delete + insert. Exactly one row is the assertion, not a dedup artifact.
    window = client.execute(
        "SELECT start_ts, horizon_ts, interval_anchor FROM abkit_internal._ab_experiments "
        "WHERE experiment = 'example_signup_test'"
    )
    assert len(window) == 1
    start_ts, horizon_ts, anchor = window[0]
    assert _naive_utc(start_ts) == datetime(2024, 7, 1)
    assert _naive_utc(horizon_ts) == datetime(2024, 7, 15)
    assert anchor == "midnight"


def test_a_pre_m10_catalog_refuses_to_migrate_and_names_the_remedy(
    clickhouse, tmp_path, monkeypatch
):
    """A TYPE change is not auto-migratable: ``ensure_columns`` is ADD-only, so
    an install carrying the pre-M10 ``start_date``/``end_date`` ``Date`` columns
    must stop with the drop-and-recreate remedy — on the terminal, not as a
    traceback — rather than half-migrate or write into the wrong shape.
    """
    from abkit.core.models import ColumnDefinition, TableModel
    from abkit.database.clickhouse_manager import ClickHouseDatabaseManager
    from abkit.database.tables import get_experiments_table_model

    _, client, conn = _prepare_project(clickhouse, tmp_path, monkeypatch, "demo_m10_stale")

    current = get_experiments_table_model()
    renamed = {"start_ts", "horizon_ts", "interval_anchor"}
    columns = []
    for column in current.columns:
        if column.name in renamed:
            continue
        columns.append(column)
        if column.name == "is_actual":
            columns += [
                ColumnDefinition("start_date", "Date"),
                ColumnDefinition("end_date", "Date"),
            ]
    manager = ClickHouseDatabaseManager(
        **conn, internal_database="abkit_internal", data_database="analytics"
    )
    try:
        manager.create_table(
            "abkit_internal._ab_experiments",
            TableModel(
                columns=columns,
                primary_key=current.primary_key,
                engine=current.engine,
                order_by=current.order_by,
                indexes=current.indexes,
                version_column=current.version_column,
            ),
        )
    finally:
        manager.close()
    assert "start_date" in _live_columns(client, "_ab_experiments")

    result = runner.invoke(cli_group(), ["run", "--select", "example_signup_test"])
    assert result.exit_code == 1
    assert "drop and recreate" in result.output.lower()
    assert "DROP TABLE abkit_internal._ab_experiments" in result.output
    # refused, not half-applied: the stale table is untouched
    live = _live_columns(client, "_ab_experiments")
    assert "start_date" in live and "start_ts" not in live


def cli_group():
    from abkit.cli.main import cli

    return cli


_ = Path  # imported for parity with the sibling module's helpers
