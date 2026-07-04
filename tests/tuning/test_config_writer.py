"""WP5 tests: Apply, ``.history``, orphan detection (m3-implementation-plan.md WP5).

Ports the donor ``test_tune_config_writer.py`` shapes — fixed ``now`` for
deterministic archive paths, byte-verbatim archives (comments included),
invalid/quarantined/empty change-sets writing NOTHING, untouched comparisons
preserved in both orderings with no phantom params, identity-excluded
carry-over — and adds the abkit-specific contracts: the orphan block present
iff the identity changed AND old rows exist, alpha-only edits orphan-free,
Review-mode role flips orphan-free but alpha-shifting, repeated-Apply archive
accumulation, and discovery never picking archives up as live configs.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
import yaml
from fake_db import FakeDatabaseManager

from abkit.config import ExperimentConfig, MetricConfig, ProjectConfig
from abkit.config.validator import discover_config_files
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline.analyze import effective_alphas
from abkit.stats import MethodParamError, QuarantinedMethodError
from abkit.tuning import TunedComparison, apply_tuned_config

NOW = datetime(2026, 7, 4, 12, 0, 0)

EXPERIMENT_YAML = """\
# precious top comment that must survive in the archive
name: exp_apply
start_date: 2024-07-01
end_date: 2024-07-04
unit_key: user_id
assignment:
  query: SELECT user_id, variant, exposure_ts FROM assignments
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: arpu
    is_main_metric: true
    method:
      name: t-test
      params: {test_type: relative}
  - metric: ctr  # inline comment
    is_guardrail: true
    method:
      name: ratio-delta
"""

METRICS = {
    "arpu": MetricConfig.model_validate(
        {
            "name": "arpu",
            "type": "sample",
            "columns": {"variant": "variant", "value": "v"},
            "query": "q",
        }
    ),
    "ctr": MetricConfig.model_validate(
        {
            "name": "ctr",
            "type": "ratio",
            "columns": {"variant": "variant", "numerator": "n", "denominator": "d"},
            "query": "q",
        }
    ),
}


@pytest.fixture
def project(tmp_path):
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    path = experiments / "exp_apply.yml"
    path.write_text(EXPERIMENT_YAML, encoding="utf-8")
    return tmp_path, path


def apply(path, root, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("metrics_by_name", METRICS)
    return apply_tuned_config(original_path=path, project_root=root, **kwargs)


def seeded_tables(experiment: str, metric: str, method_config_id: str, rows: int = 3):
    """A fake manager with persisted result rows under one id."""
    manager = FakeDatabaseManager()
    tables = InternalTablesManager(manager)
    tables.ensure_tables()
    n = rows
    data = {
        "experiment": np.array([experiment] * n, dtype=object),
        "metric": np.array([metric] * n, dtype=object),
        "method_config_id": np.array([method_config_id] * n, dtype=object),
        "name_1": np.array(["control"] * n, dtype=object),
        "name_2": np.array(["treatment"] * n, dtype=object),
        "end_ts": np.array([datetime(2024, 7, 2 + i) for i in range(n)], dtype=object),
    }
    manager.insert_batch("_ab_results", data)
    return tables


class TestApplyHappyPath:
    def test_validate_archive_reemit(self, project):
        root, path = project
        original = path.read_bytes()
        result = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )

        assert result.experiment == "exp_apply"
        assert result.updated == ("arpu",)
        assert result.preserved == ("ctr",)
        # the archive is the ORIGINAL, byte-verbatim — comments included
        assert result.archived.read_bytes() == original
        assert result.archived.name == "exp_apply-20260704T120000Z.yml"
        assert ".history" in str(result.archived)

        saved = ExperimentConfig.from_yaml_file(path)
        assert saved.comparisons[0].method.params == {"test_type": "absolute"}
        text = path.read_text(encoding="utf-8")
        assert "Hand-tuned via `abk explore`" in text
        assert "Reproduce: abk explore --select exp_apply" in text
        assert str(result.archived.relative_to(root)) in text

    @pytest.mark.parametrize("reverse", [False, True])
    def test_untouched_comparisons_preserved_no_phantom_params(self, tmp_path, reverse):
        raw = yaml.safe_load(EXPERIMENT_YAML)
        if reverse:
            raw["comparisons"] = list(reversed(raw["comparisons"]))
        experiments = tmp_path / "experiments"
        experiments.mkdir()
        path = experiments / "exp_apply.yml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        before_ctr = next(c for c in raw["comparisons"] if c["metric"] == "ctr")

        apply(
            root=tmp_path,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        after_ctr = next(c for c in after["comparisons"] if c["metric"] == "ctr")
        assert after_ctr == before_ctr  # verbatim at the document level
        assert [c["metric"] for c in after["comparisons"]] == [
            c["metric"] for c in raw["comparisons"]
        ]

    def test_identity_excluded_carryover(self, tmp_path):
        raw = yaml.safe_load(EXPERIMENT_YAML)
        raw["comparisons"][0]["method"] = {
            "name": "bootstrap",
            "params": {"test_type": "relative", "n_samples": 200, "max_block_bytes": 4096},
        }
        experiments = tmp_path / "experiments"
        experiments.mkdir()
        path = experiments / "exp_apply.yml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        apply(
            root=tmp_path,
            path=path,
            comparisons=[
                TunedComparison("arpu", params={"test_type": "relative", "n_samples": 500})
            ],
        )
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        params = saved["comparisons"][0]["method"]["params"]
        assert params["n_samples"] == 500
        assert params["max_block_bytes"] == 4096  # carried over, never dropped

    def test_method_switch_and_experiment_fields(self, project):
        root, path = project
        result = apply(
            root=root,
            path=path,
            comparisons=[
                TunedComparison(
                    "arpu",
                    method_name="bootstrap",
                    params={"test_type": "relative", "n_samples": 300},
                )
            ],
            alpha=0.01,
            correction="none",
        )
        assert result.experiment_fields == ("alpha=0.01", "correction=none")
        saved = ExperimentConfig.from_yaml_file(path)
        assert saved.comparisons[0].method.name == "bootstrap"
        assert saved.alpha == 0.01
        assert saved.correction == "none"

    def test_review_role_flip_is_orphan_free_but_shifts_alphas(self, project):
        root, path = project
        before = ExperimentConfig.from_yaml_file(path)
        project_cfg = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})
        alphas_before = effective_alphas(before, project_cfg)

        tables = seeded_tables("exp_apply", "arpu", before.comparisons[0].method.method_config_id)
        # re-mark: the main-metric role moves from arpu to ctr in one Apply
        result = apply(
            root=root,
            path=path,
            comparisons=[
                TunedComparison("arpu", is_main_metric=False, is_guardrail=True),
                TunedComparison("ctr", is_main_metric=True, is_guardrail=False),
            ],
            tables=tables,
        )
        assert result.orphaned == ()  # marking only — identity untouched
        after = ExperimentConfig.from_yaml_file(path)
        assert after.comparisons[0].is_main_metric is False
        assert after.comparisons[1].is_main_metric is True
        alphas_after = effective_alphas(after, project_cfg)
        assert alphas_before == alphas_after  # 2 comparisons, 1 main — same tiers
        # the per-comparison assignment still moved: the main tier now belongs
        # to ctr, so arpu verdicts read the secondary alpha
        assert before.comparisons[0].is_main_metric is True

    def test_nested_experiment_form(self, tmp_path):
        raw = {"experiment": yaml.safe_load(EXPERIMENT_YAML)}
        experiments = tmp_path / "experiments"
        experiments.mkdir()
        path = experiments / "exp_apply.yml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        apply(
            root=tmp_path,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "experiment" in saved  # the wrapper survives
        assert ExperimentConfig.from_yaml_file(path).comparisons[0].method.params == {
            "test_type": "absolute"
        }


class TestNothingWrittenOnFailure:
    @pytest.fixture
    def untouched(self, project):
        root, path = project
        before = path.read_bytes()
        yield root, path
        assert path.read_bytes() == before  # the file never changed
        assert not (path.parent / ".history").exists()  # and nothing archived

    def test_empty_change_set(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="nothing to apply"):
            apply(root=root, path=path, comparisons=[])

    def test_invalid_params(self, untouched):
        root, path = untouched
        with pytest.raises(MethodParamError):
            apply(
                root=root,
                path=path,
                comparisons=[TunedComparison("arpu", params={"test_type": "sideways"})],
            )

    def test_quarantined_method(self, untouched):
        root, path = untouched
        with pytest.raises(QuarantinedMethodError):
            apply(
                root=root,
                path=path,
                comparisons=[
                    TunedComparison("arpu", method_name="poisson-post-normed-bootstrap", params={})
                ],
            )

    def test_paired_method_refused(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="paired design"):
            apply(
                root=root,
                path=path,
                comparisons=[TunedComparison("arpu", method_name="paired-t-test", params={})],
            )

    def test_cross_kind_method_refused(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="expects a 'fraction' metric"):
            apply(
                root=root,
                path=path,
                comparisons=[TunedComparison("arpu", method_name="z-test", params={})],
            )

    def test_unknown_metric(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="not found in the experiment"):
            apply(
                root=root,
                path=path,
                comparisons=[TunedComparison("nope", params={})],
            )

    def test_method_switch_without_params(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="full param set"):
            apply(
                root=root, path=path, comparisons=[TunedComparison("arpu", method_name="bootstrap")]
            )

    def test_duplicate_comparison_in_one_apply(self, untouched):
        root, path = untouched
        with pytest.raises(ValueError, match="appears twice"):
            apply(
                root=root,
                path=path,
                comparisons=[
                    TunedComparison("arpu", params={"test_type": "absolute"}),
                    TunedComparison("arpu", params={"test_type": "relative"}),
                ],
            )


class TestOrphanDetection:
    def test_orphan_block_iff_id_changed_and_rows_exist(self, project):
        root, path = project
        before = ExperimentConfig.from_yaml_file(path)
        old_id = before.comparisons[0].method.method_config_id
        tables = seeded_tables("exp_apply", "arpu", old_id, rows=3)

        result = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
            tables=tables,
        )
        assert len(result.orphaned) == 1
        orphan = result.orphaned[0]
        assert orphan.metric == "arpu"
        assert orphan.old_id == old_id
        assert orphan.rows == 3
        assert orphan.new_id != old_id
        warning = result.orphan_warning
        assert "abk clean" in warning
        assert "abk run --select exp_apply" in warning
        assert "duplicate stabilization lines" in warning
        assert warning in path.read_text(encoding="utf-8")  # surfaced in the header

    def test_no_orphan_without_stored_rows(self, project):
        root, path = project
        tables = seeded_tables("exp_apply", "arpu", "some-other-id", rows=2)
        result = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
            tables=tables,
        )
        assert result.orphaned == ()
        assert result.orphan_warning is None

    def test_alpha_only_edit_is_orphan_free(self, project):
        root, path = project
        before = ExperimentConfig.from_yaml_file(path)
        tables = seeded_tables("exp_apply", "arpu", before.comparisons[0].method.method_config_id)
        result = apply(root=root, path=path, alpha=0.01, tables=tables)
        assert result.orphaned == ()
        assert result.updated == ()
        assert ExperimentConfig.from_yaml_file(path).alpha == 0.01

    def test_no_scan_without_tables(self, project):
        root, path = project
        result = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )
        assert result.orphaned == ()


class TestArchiveAccumulation:
    def test_repeated_applies_each_archive(self, project):
        root, path = project
        first = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
            now=datetime(2026, 7, 4, 12, 0, 0),
        )
        second = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "relative"})],
            now=datetime(2026, 7, 4, 12, 5, 0),
        )
        assert first.archived != second.archived
        assert first.archived.exists() and second.archived.exists()
        # the second archive holds the FIRST apply's output (each Apply archives
        # its predecessor verbatim)
        assert "Hand-tuned via `abk explore`" in second.archived.read_text(encoding="utf-8")

    def test_same_second_applies_do_not_clobber(self, project):
        root, path = project
        first = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )
        second = apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "relative"})],
        )
        assert first.archived != second.archived
        assert second.archived.name.endswith("-2.yml")

    def test_archives_are_never_discovered_as_live_configs(self, project):
        root, path = project
        apply(
            root=root,
            path=path,
            comparisons=[TunedComparison("arpu", params={"test_type": "absolute"})],
        )
        live = discover_config_files(path.parent)
        assert live == [path]  # .history is invisible to discovery
