"""Guards for the BI reference examples (`docs/examples/bi/`) — M6 WP4.

These are documentation, not shipped code, but they are a stated contract
(`_ab_results` is the BI table). This suite keeps the recipes from silently
drifting from the real schema: if a `_ab_results` column is renamed, the
column cross-check fails so the recipes get updated in the same change.
No database required.
"""

import json
from pathlib import Path

from abkit.database.internal_tables._results import RESULT_COLUMNS

BI = Path(__file__).resolve().parents[2] / "docs" / "examples" / "bi"

# The `_ab_results` columns the recipes bind to. Kept explicit (not scraped) so a
# schema rename produces a clear, reviewable failure here.
COLUMNS_USED = {
    "experiment", "metric", "is_main_metric", "is_guardrail", "method_config_id",
    "method_name", "name_1", "name_2", "start_ts", "end_ts", "elapsed_days",
    "effect", "left_bound", "right_bound", "ci_length", "ci_kind", "pvalue",
    "alpha", "reject", "value_1", "value_2", "std_1", "std_2", "cov_value_1",
    "cov_value_2", "size_1", "size_2", "mde_1", "mde_2", "srm_flag", "srm_pvalue",
    "decision_blocked", "insufficient_data", "is_horizon", "created_at",
}


def test_bi_folder_ships_the_expected_files():
    for name in ("README.md", "queries.sql", "srm_panel.sql", "grafana_dashboard.json"):
        assert (BI / name).is_file(), f"docs/examples/bi/{name} is missing"


def test_recipe_columns_exist_in_results_schema():
    """Every column the recipes depend on is a real `_ab_results` column."""
    schema = set(RESULT_COLUMNS) | {"created_at"}
    missing = COLUMNS_USED - schema
    assert not missing, f"BI recipes reference columns not in _ab_results: {sorted(missing)}"


def test_sql_recipes_dedup_and_target_the_contract_table():
    """Every SQL recipe reads the contract table FINAL (the ClickHouse dedup invariant)."""
    for name in ("queries.sql", "srm_panel.sql"):
        body = (BI / name).read_text(encoding="utf-8")
        assert "abkit_internal._ab_results" in body, f"{name} doesn't target _ab_results"
        assert "FINAL" in body, f"{name} omits FINAL (ClickHouse dedup invariant)"


def test_grafana_dashboard_is_valid_and_wired():
    dash = json.loads((BI / "grafana_dashboard.json").read_text(encoding="utf-8"))
    assert dash["panels"], "no panels"
    targets = [t for p in dash["panels"] for t in p.get("targets", [])]
    assert targets, "no panel targets"
    for t in targets:
        assert "_ab_results" in t["rawSql"], "a panel target doesn't query _ab_results"
