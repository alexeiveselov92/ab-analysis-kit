"""m14 DEC-3 hold, opened by DEC-4: explore's payload stays control-anchored.

The report payload rides into the explore payload VERBATIM
(`abkit/tuning/payload.py`), and since DEC-3 its ``verdicts`` list carries a
verdict for every declared pair. Review mode renders every verdict whose metric
matches (M7 WP0 turned `.find` into `.filter`) and prints the WORD alone — so a
`WIN` on `B vs C` would read there as a ship recommendation, which is exactly
the misreading `role` was added to stop, on the one surface that cannot yet say
it. DEC-4 adds the Review-mode role label and the rollup line, and drops the
filter; this file is what makes that a deliberate commit rather than a leak.
"""

from __future__ import annotations

from synthetic_ab import (
    SyntheticWarehouse,
    build_session,
    make_experiment,
    seed_all_events,
    seed_cohort,
)

from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import RecomputeEngine, build_explore_payload

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}


def _session_and_engine():
    """A session over an experiment with no persisted rows — the verdicts under
    test are injected, so nothing here needs a pipeline run."""
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse)
    seed_all_events(warehouse)
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()
    experiment = make_experiment("explore_holds", "arpu", T_TEST)
    session = build_session(warehouse, tables, experiment)
    return session, RecomputeEngine(session)


def _verdict(name_1: str, name_2: str, role: str, metric: str = "arpu") -> dict:
    return {"metric": metric, "pair": {"c": name_1, "t": name_2}, "verdict": "WIN", "role": role}


def test_treatment_pair_verdicts_do_not_reach_the_cockpit():
    """The fixture is shaped to defeat three WRONG filters, not just to pass.

    Two ship decisions on ONE metric, so "keep the first verdict" — the M7 WP0
    `.find`-instead-of-`.filter` bug, on the server side this time — loses the
    second and fails here. A third on another metric, so a metric-scoped filter
    fails too. And the control is `c`, declared LAST, so a positional
    `pair.c == variants[0]` filter inverts: it would keep the treatment pair and
    drop both ship decisions.
    """
    session, engine = _session_and_engine()
    report = {
        "experiment": "explore_holds",
        "verdicts": [
            _verdict("c", "a", "vs_control"),
            _verdict("c", "b", "vs_control"),
            _verdict("c", "a", "vs_control", metric="orders"),
            _verdict("a", "b", "treatment_pair"),
        ],
    }
    original = [dict(v) for v in report["verdicts"]]

    payload = build_explore_payload(session, engine, report)

    assert [(v["metric"], v["pair"]["c"], v["pair"]["t"]) for v in payload["verdicts"]] == [
        ("arpu", "c", "a"),
        ("arpu", "c", "b"),
        ("orders", "c", "a"),
    ]
    # non-destructive: the caller's payload is the one `abk run --report` may
    # still bake, so the hold must filter a COPY
    assert report["verdicts"] == original


def test_a_pre_0_9_0_report_payload_keeps_every_verdict():
    """No ``role`` key means a payload baked before 0.9.0, and every one of
    those lists is control-anchored by construction — the filter must not
    silence a cockpit replaying an older bake."""
    session, engine = _session_and_engine()
    report = {"experiment": "explore_holds", "verdicts": [{"metric": "arpu", "verdict": "FLAT"}]}

    payload = build_explore_payload(session, engine, report)

    assert payload["verdicts"] == [{"metric": "arpu", "verdict": "FLAT"}]
