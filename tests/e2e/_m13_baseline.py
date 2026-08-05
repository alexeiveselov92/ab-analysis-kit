"""m13 STAT-6 — the byte-compatibility capture, runnable at ``0.7.0`` AND at HEAD.

The milestone's №1 assertion (m13-implementation-plan.md §4.1) is that a project
which writes nothing new reproduces the previous release exactly. Proving that
needs a surface captured from **the released code itself** — comparing HEAD with
HEAD proves nothing, which is the M10 window-golden lesson.

This module is the capture, and it is deliberately written against only what
``v0.7.0`` already shipped: the scaffold assets, ``tests/_helpers/`` and
``tests/e2e/test_first_run.py`` are byte-identical between ``v0.7.0`` and HEAD
(``git diff v0.7.0 HEAD -- tests/_helpers/ tests/e2e/test_first_run.py
abkit/cli/assets/project/`` is empty), so the same file runs unmodified in both
checkouts and the two surfaces are comparable by construction.

**Regenerating the golden** (only ever from a released checkout — never from
HEAD)::

    git worktree add /tmp/abk-070 v0.7.0
    cp tests/e2e/_m13_baseline.py /tmp/abk-070/tests/e2e/
    .venv/bin/python /tmp/abk-070/tests/e2e/_m13_baseline.py \\
        tests/e2e/fixtures/results_golden_0_7_0.json

The ``__main__`` block pins ``abkit`` to the checkout it lives in — it drops the
editable install's ``sys.meta_path`` finder and prepends the checkout — and then
**asserts** where ``abkit`` actually resolved, so a capture cannot silently be
taken from the wrong tree. The assert is the guarantee; the removal is
belt-and-braces, since ``PathFinder`` happens to precede the editable finder
today.

**``MULTI_ARM_PAYLOAD`` is part of the golden's identity.** Editing it makes the
committed surface unreproducible, and the failure looks like a moved number
rather than an edited fixture — regenerate from the released checkout, or add a
new payload beside it.

Two surfaces, because the scaffold alone cannot reach the defaults M13 moved
around:

* ``scaffold`` — ``abk init`` + ``abk run`` exactly as shipped (scaffold
  defaults included, ``incremental_reads: true`` since PERF-1). Two arms, two
  metrics, ``z-test`` + ``cuped-t-test``.
* ``multi_arm`` — three arms and five comparisons over the synthetic warehouse:
  the ``C(3,2)`` contrast family (STAT-1b's ``all_pairs`` default), a guardrail
  comparison (STAT-1c's ``inherit`` default), ``z-test`` (STAT-3's ``pooled``
  default), three mean methods on the relative scale (STAT-4's ``delta``
  default), and one bootstrap method (untouched by M13, so its seeds pin that
  ``BaseMethod.__init__``'s new param folding moved nothing).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

#: The scaffolded window closes on 2024-07-15; any later instant makes every
#: cutoff due. Same value the M2 first-run gate uses.
SCAFFOLD_NOW = datetime(2024, 8, 1)

MULTI_ARM_UNITS_PER_ARM = 60

#: Three arms, five comparisons — one per default M13 touched, plus a bootstrap
#: it did not. Three of them read the same revenue facts under different metric
#: names, because a metric may be bound at most once per experiment.
MULTI_ARM_PAYLOAD = {
    "name": "m13_multi_arm",
    "start_ts": "2024-07-01",
    "horizon_ts": "2024-07-05",
    "unit_key": "user_id",
    "alpha": 0.05,
    "assignment": {
        "query": "SELECT user_id, variant, exposure_ts FROM assignments",
        "variants": ["control", "treatment", "challenger"],
        "expected_split": {"control": 1 / 3, "treatment": 1 / 3, "challenger": 1 / 3},
    },
    "comparisons": [
        {"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}},
        {
            "metric": "arpu_cuped",
            "method": {
                "name": "cuped-t-test",
                "params": {"test_type": "relative", "covariate_lookback": "7d"},
            },
        },
        {"metric": "arpu_boot", "method": {"name": "bootstrap", "params": {"n_samples": 300}}},
        {"metric": "conversion", "method": {"name": "z-test"}},
        {"metric": "ctr", "is_guardrail": True, "method": {"name": "ratio-delta"}},
    ],
}

#: A metric may be bound at most once per experiment, so the two extra mean
#: comparisons need their own names. Same SQL as ``synthetic_ab.REVENUE``,
#: repeated here rather than imported so this file keeps running unchanged in
#: an older checkout whatever that helper looks like there.
_REVENUE_SQL = (
    "{% import 'abkit_assignment.jinja' as ab %}\n"
    "SELECT {{ ab.variant_col() }} AS variant, user_id, sum(gross_usd) AS gross_usd "
    "FROM {{ data_database }}.user_revenue {{ ab.exposed_units() }} "
    "GROUP BY variant, user_id"
)


#: Dropped before comparison: wall-clock stamps and the temp-dir the scaffold
#: was built in. Nothing statistical, and all three differ between any two runs
#: of the SAME code — keeping them would make the gate fail for the one reason
#: it must not.
VOLATILE_COLUMNS = frozenset({"created_at", "updated_at", "path"})


#: The identity columns, in the order rows are sorted by. DISCRETE on purpose:
#: sorting by the whole row would let a last-ULP float difference REORDER the
#: list, and the comparison — which tolerates rel-1e-9 on continuous columns
#: precisely because byte reproducibility across BLAS configurations is not a
#: property this project has (M7 D13) — would then compare mismatched pairs.
ROW_ORDER = ("experiment", "metric", "method_config_id", "name_1", "name_2", "end_ts")


def _canonical(rows) -> list[dict]:
    """Strip the volatile columns and put the rows in a stable, discrete order."""
    stripped = [{k: v for k, v in dict(row).items() if k not in VOLATILE_COLUMNS} for row in rows]
    return sorted(
        (json.loads(json.dumps(row, sort_keys=True, default=str)) for row in stripped),
        key=lambda row: tuple(str(row.get(column, "")) for column in ROW_ORDER),
    )


def capture_scaffold_surface() -> dict[str, list[dict]]:
    """``abk init demo && abk run`` against the seed-mirror warehouse."""
    from test_first_run import SeedMirrorWarehouse

    import abkit.config.profile as profile_mod
    import abkit.pipeline.driver as driver_mod
    from abkit.cli.main import cli

    runner = CliRunner()
    warehouse = SeedMirrorWarehouse()
    with runner.isolated_filesystem():
        created = runner.invoke(cli, ["init", "demo"])
        assert created.exit_code == 0, created.output
        outer = os.getcwd()
        os.chdir("demo")
        try:
            with (
                mock.patch.object(
                    profile_mod.ProfileConfig, "create_manager", lambda self: warehouse
                ),
                mock.patch.object(driver_mod, "now_utc_naive", lambda: SCAFFOLD_NOW),
            ):
                result = runner.invoke(cli, ["run", "--select", "example_signup_test"])
                assert result.exit_code == 0, result.output
        finally:
            os.chdir(outer)
    return {
        "_ab_results": _canonical(warehouse._rows["_ab_results"]),
        "_ab_experiments": _canonical(warehouse._rows["_ab_experiments"]),
    }


def build_multi_arm_context():
    """A seeded warehouse + its tables manager + the metric library.

    Split out so a caller can run TWO configurations against the SAME storage —
    which is the only way to assert that opting in leaves the previous series
    beside the new one rather than merely producing different rows in two
    different warehouses.
    """
    from synthetic_ab import METRICS, START, SyntheticWarehouse, seed_all_events

    from abkit.config import MetricConfig
    from abkit.database.internal_tables import InternalTablesManager

    metrics = dict(METRICS)
    for name in ("arpu_cuped", "arpu_boot"):
        metrics[name] = MetricConfig.model_validate(
            {
                "name": name,
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd"},
                "query": _REVENUE_SQL,
            }
        )

    warehouse = SyntheticWarehouse()
    for index in range(MULTI_ARM_UNITS_PER_ARM):
        for prefix, arm in (("c", "control"), ("t", "treatment"), ("h", "challenger")):
            warehouse.cohort.append((f"{prefix}{index:03d}", arm, START + timedelta(hours=1)))
    seed_all_events(warehouse, days=4)
    return warehouse, InternalTablesManager(warehouse), metrics


def capture_multi_arm_surface(payload: dict | None = None, context=None) -> dict[str, list[dict]]:
    """Three arms × five comparisons through the real pipeline driver.

    ``payload`` overrides the experiment document — the STAT-6 gate runs the
    same fixture again with one knob turned to show what a knob costs; the
    golden is always captured with the default. ``context`` reuses a warehouse
    from :func:`build_multi_arm_context`, so successive calls ACCUMULATE rows.
    """
    from synthetic_ab import NOW, PROJECT

    from abkit.config import ExperimentConfig
    from abkit.pipeline import run_experiment

    warehouse, tables, metrics = context if context is not None else build_multi_arm_context()
    experiment = ExperimentConfig.model_validate(payload or MULTI_ARM_PAYLOAD)
    outcome = run_experiment(experiment, metrics, PROJECT, warehouse, tables, now_utc=NOW)
    assert outcome.status == "completed", outcome.error
    return {"_ab_results": _canonical(warehouse._rows["_ab_results"])}


def capture_all() -> dict:
    import abkit

    return {
        "abkit_version": abkit.__version__,
        "scaffold": capture_scaffold_surface(),
        "multi_arm": capture_multi_arm_surface(),
    }


def _pin_abkit_to_this_checkout() -> None:
    """Make ``import abkit`` resolve to the tree this file lives in.

    A PEP-660 editable install answers from a ``sys.meta_path`` finder, which
    precedes every ``sys.path`` entry — so prepending the checkout is not
    enough, and a capture would silently be taken from the working tree it was
    meant to be independent of.
    """
    root = Path(__file__).resolve().parents[2]
    for finder in list(sys.meta_path):
        # The finder is registered as a CLASS, so `type(finder).__module__` is
        # "builtins" — read the object's own module and name, case-folded.
        origin = f"{getattr(finder, '__module__', '')}{getattr(finder, '__name__', '')}"
        if "editable" in origin.lower():
            sys.meta_path.remove(finder)
    for path in (str(root), str(root / "tests" / "_helpers"), str(root / "tests" / "e2e")):
        sys.path.insert(0, path)
    import abkit

    assert (
        Path(abkit.__file__).resolve().is_relative_to(root)
    ), f"captured from the wrong tree: {abkit.__file__} is not under {root}"


if __name__ == "__main__":
    _pin_abkit_to_this_checkout()
    destination = Path(sys.argv[1]).resolve()
    surface = capture_all()
    destination.write_text(json.dumps(surface, indent=1, sort_keys=True) + "\n")
    counts = {
        name: {k: len(v) for k, v in body.items()}
        for name, body in surface.items()
        if isinstance(body, dict)
    }
    print(f"captured abkit {surface['abkit_version']} → {destination}: {counts}")
