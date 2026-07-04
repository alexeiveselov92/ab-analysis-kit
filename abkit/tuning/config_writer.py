"""Write a tuned experiment config back to its YAML — safely (WP5, D4).

The ONLY mutation seam of ``abk explore``. Order is validate → archive →
re-emit (the donor's discipline): a broken config never lands, and the
previous file is always recoverable **byte-verbatim** from
``<dir>/.history/<experiment>/`` (discovery excludes hidden dirs, so archives
are never picked up as live configs).

Apply targets ONE file — the experiment YAML (method params live on
``ComparisonConfig``; metric YAMLs are never touched, which also keeps the
analysis-unit knob preview-only). Write-back **merges**: each
:class:`TunedComparison` rewrites only its own comparison (matched by
metric); every comparison the cockpit didn't touch is preserved, and
experiment-level ``alpha``/``correction`` edits ride the same Apply.

Orphaning (D4, NEW vs the donor): before writing, the old-vs-new
``method_config_id`` is computed per touched comparison through the single
hashing path (``MethodConfig`` → the bound probe), and when the id changes
over a series with persisted rows the result carries an ``orphaned`` block —
the warning text mirrors the driver's. Apply **never** auto-cleans or
auto-runs: ``clean`` deletes data and ``run`` takes locks — both deserve
explicit intent.

YAML comments die on safe_load → re-emit; the verbatim archive is the
recovery (owner-ratified, D4). Re-emission is isolated behind ONE strategy
function — :func:`_reemit_yaml` — so a comment-preserving ruamel backend can
swap in later without touching the Apply contract (the ROADMAP backlog note).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.method_config import MethodConfig
from abkit.config.metric_config import MetricConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.stats import create_method, get_method_class

_RULE = "# " + "─" * 61


@dataclass(frozen=True)
class TunedComparison:
    """One comparison the cockpit is writing back, matched by ``metric``.

    ``params`` is the FULL method param set for the dirty comparison (the
    cockpit sends everything it shows — the donor's dirty-slot discipline:
    the opened comparison is always written, a merely-viewed one never is).
    ``method_name=None`` keeps the configured method name; ``params=None``
    with role flips only is a Review-mode marking edit (D9).
    """

    metric: str
    method_name: str | None = None
    params: Mapping[str, Any] | None = None
    is_main_metric: bool | None = None
    is_guardrail: bool | None = None


@dataclass(frozen=True)
class OrphanedSeries:
    """A touched comparison whose identity edit strands persisted rows."""

    metric: str
    old_id: str
    new_id: str
    rows: int


@dataclass(frozen=True)
class AppliedConfig:
    """Result of one Apply: paths, what changed, and the orphaning consequence."""

    experiment: str
    saved: Path
    archived: Path
    updated: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    experiment_fields: tuple[str, ...] = ()
    orphaned: tuple[OrphanedSeries, ...] = ()

    @property
    def orphan_warning(self) -> str | None:
        """The driver-identical warning line for the confirm dialog / reply /
        CLI epilogue (driver.py's orphan scan wording + the re-run hint)."""
        if not self.orphaned:
            return None
        parts = ", ".join(
            f"{o.metric} ({o.rows} rows under {o.old_id[:12]}…)" for o in self.orphaned
        )
        return (
            f"{self.experiment}: orphaned method_config_id series in _ab_results — "
            f"{parts} (the BI chart will show duplicate stabilization lines) — run "
            f"`abk clean`, then `abk run --select {self.experiment}`"
        )


def _stamp(now: datetime | None = None) -> str:
    """UTC filesystem-safe timestamp (``20260704T101530Z``)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _reemit_yaml(document: dict[str, Any], original_bytes: bytes) -> bytes:
    """THE document re-emission strategy (D4's designed seam).

    PyYAML ``safe_dump`` today — comments die, the archive is the recovery. A
    comment-preserving ruamel backend can replace this one function (using
    ``original_bytes`` for round-trip state) without touching the Apply
    contract, the validate→archive→re-emit order, or archive semantics.
    Nothing else in this module may serialize YAML.
    """
    del original_bytes  # unused by the PyYAML strategy
    return yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False, allow_unicode=True
    ).encode("utf-8")


def _identity_excluded_carryover(
    method_cls: type, old_params: Mapping[str, Any], params: dict[str, Any]
) -> None:
    """Carry identity-excluded params (``seed``, ``max_block_bytes``) from the
    slot being retuned unless the cockpit supplied them — the donor's
    execution-param carry-over, derived from the specs, never a hardcode."""
    for spec in method_cls.param_specs:
        if not spec.identity and spec.name in old_params and spec.name not in params:
            params[spec.name] = old_params[spec.name]


def _method_config_id_of(entry: Mapping[str, Any]) -> str | None:
    """The canonical id of a comparison entry's method block, or ``None`` when
    the stored block no longer binds (a legacy/broken entry never blocks Apply)."""
    method = entry.get("method")
    if not isinstance(method, Mapping) or not method.get("name"):
        return None
    try:
        return MethodConfig(
            name=str(method["name"]), params=dict(method.get("params") or {})
        ).method_config_id
    except Exception:
        return None


def _header(
    experiment: str,
    archive_rel: str,
    stamp: str,
    updated: list[str],
    preserved: list[str],
    experiment_fields: list[str],
    orphan_warning: str | None,
) -> str:
    changed = f"# Updated comparison(s): {', '.join(updated) if updated else 'none'}"
    if preserved:
        changed += f"; preserved: {', '.join(preserved)}"
    if experiment_fields:
        changed += f". Experiment-level: {', '.join(experiment_fields)}."
    lines = [
        _RULE,
        f"# Hand-tuned via `abk explore`  ({stamp})",
        f"# Previous config archived at: {archive_rel}",
        changed,
    ]
    if orphan_warning:
        lines.append(f"# ⚠ {orphan_warning}")
    lines += [f"# Reproduce: abk explore --select {experiment}", _RULE]
    # every line must stay ONE comment line — a newline smuggled through a
    # name would inject uncommented text into the emitted YAML
    return "\n".join(line.replace("\n", " ").replace("\r", " ") for line in lines)


def _merge_comparison(
    entries: list[Any],
    tuned: TunedComparison,
    metrics_by_name: Mapping[str, MetricConfig] | None,
) -> tuple[int, dict[str, Any], str | None, str | None]:
    """Validate one tuned comparison and return its merged entry.

    Returns ``(slot, new_entry, old_id, new_id)``. Raises ``ValueError``
    (nothing written upstream) on an unknown metric, a non-tunable method
    (paired / wrong input kind — tunability is registry-derived, never a
    hardcoded name set), or invalid params.
    """
    slot = next(
        (
            i
            for i, entry in enumerate(entries)
            if isinstance(entry, Mapping) and entry.get("metric") == tuned.metric
        ),
        None,
    )
    if slot is None:
        raise ValueError(
            f"comparison for metric '{tuned.metric}' not found in the experiment "
            "(Apply edits existing comparisons; it never invents new ones)"
        )
    entry = dict(entries[slot])
    old_id = _method_config_id_of(entry)

    if tuned.params is None and tuned.method_name is not None:
        raise ValueError(
            f"comparison '{tuned.metric}': a method switch must carry the full "
            "param set (the cockpit always sends what it shows)"
        )

    new_id = old_id
    if tuned.params is not None:
        old_method = entry.get("method") if isinstance(entry.get("method"), Mapping) else {}
        old_name = str(old_method.get("name", "")) if old_method else ""
        old_params = dict(old_method.get("params") or {}) if old_method else {}
        name = tuned.method_name or old_name
        if not name:
            raise ValueError(f"comparison '{tuned.metric}': no method name to apply")

        method_cls = get_method_class(name)  # Unknown/Quarantined surface verbatim
        if method_cls.is_paired:
            raise ValueError(
                f"method '{name}' is a paired design — not tunable from explore "
                "(the pipeline serves independent-arm experiments)"
            )
        if metrics_by_name is not None and tuned.metric in metrics_by_name:
            metric_type = metrics_by_name[tuned.metric].type
            if method_cls.input_kind != metric_type:
                raise ValueError(
                    f"method '{name}' expects a '{method_cls.input_kind}' metric, "
                    f"got '{metric_type}' ({tuned.metric})"
                )

        params = {k: v for k, v in dict(tuned.params).items() if k != "name"}
        _identity_excluded_carryover(method_cls, old_params, params)
        # Validate by actually constructing the method (its ParamSpecs run);
        # alpha is experiment-level and does not affect param validation.
        create_method(name, alpha=0.05, params=dict(params))

        entry["method"] = {"name": name, "params": params} if params else {"name": name}
        new_id = MethodConfig(name=name, params=dict(params)).method_config_id

    if tuned.is_main_metric is not None:
        entry["is_main_metric"] = bool(tuned.is_main_metric)
    if tuned.is_guardrail is not None:
        entry["is_guardrail"] = bool(tuned.is_guardrail)

    return slot, entry, old_id, new_id


def apply_tuned_config(
    *,
    original_path: Path,
    project_root: Path,
    comparisons: list[TunedComparison] | None = None,
    alpha: float | None = None,
    correction: str | None = None,
    tables: InternalTablesManager | None = None,
    metrics_by_name: Mapping[str, MetricConfig] | None = None,
    now: datetime | None = None,
) -> AppliedConfig:
    """Validate, archive, then re-emit the experiment YAML with the edits merged.

    The single Apply seam (D4): per-comparison ``method`` blocks, Review-mode
    role flips, and experiment-level ``alpha``/``correction`` — merged into
    the parsed document, validated as a whole (``ExperimentConfig``), archived
    byte-verbatim, then re-emitted through :func:`_reemit_yaml`. Raises
    ``ValueError`` / the stats-core config exceptions (writing NOTHING) on any
    invalid input or an empty change-set.

    ``tables`` (optional) enables the orphan scan: an identity-changing edit
    over a series with persisted rows yields the ``orphaned`` block + the
    driver-identical warning. Apply never auto-cleans and never auto-runs.
    """
    comparisons = comparisons or []
    if not comparisons and alpha is None and correction is None:
        raise ValueError("nothing to apply: no comparison edits and no experiment-level changes")

    original_bytes = original_path.read_bytes()
    raw = yaml.safe_load(original_bytes.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"experiment config is empty or malformed: {original_path}")

    # Support the nested `experiment: {...}` form (ExperimentConfig.from_yaml_file).
    nested = isinstance(raw.get("experiment"), dict)
    body: dict[str, Any] = raw["experiment"] if nested else raw

    entries = body.get("comparisons")
    merged: list[Any] = list(entries) if isinstance(entries, list) else []

    updated_slots: set[int] = set()
    updated_metrics: list[str] = []
    id_changes: list[tuple[str, str | None, str | None]] = []
    for tuned in comparisons:
        slot, entry, old_id, new_id = _merge_comparison(merged, tuned, metrics_by_name)
        if slot in updated_slots:
            raise ValueError(f"comparison '{tuned.metric}' appears twice in one Apply")
        merged[slot] = entry
        updated_slots.add(slot)
        updated_metrics.append(tuned.metric)
        id_changes.append((tuned.metric, old_id, new_id))

    body["comparisons"] = merged
    preserved_metrics = [
        str(entry.get("metric"))
        for i, entry in enumerate(merged)
        if i not in updated_slots and isinstance(entry, Mapping)
    ]

    experiment_fields: list[str] = []
    if alpha is not None:
        body["alpha"] = alpha
        experiment_fields.append(f"alpha={alpha:g}")
    if correction is not None:
        body["correction"] = correction
        experiment_fields.append(f"correction={correction}")

    # Validate the WHOLE merged document before touching the filesystem.
    validated = ExperimentConfig.model_validate(body)
    experiment_name = validated.name

    # Orphan detection (D4): identity changed AND persisted rows under the old id.
    orphaned: list[OrphanedSeries] = []
    if tables is not None:
        for metric, old_id, new_id in id_changes:
            if old_id is None or new_id is None or old_id == new_id:
                continue
            stored = tables.list_method_config_ids(experiment_name, metric)
            rows = stored.get((metric, old_id), 0)
            if rows:
                orphaned.append(
                    OrphanedSeries(metric=metric, old_id=old_id, new_id=new_id, rows=rows)
                )

    # Archive the previous file byte-verbatim; only then overwrite.
    stamp = _stamp(now)
    archive_dir = original_path.parent / ".history" / experiment_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{experiment_name}-{stamp}.yml"
    suffix = 1
    while archive_path.exists():  # rapid same-second Applies must not clobber
        suffix += 1
        archive_path = archive_dir / f"{experiment_name}-{stamp}-{suffix}.yml"
    archive_path.write_bytes(original_bytes)

    try:
        archive_rel = str(archive_path.relative_to(project_root))
    except ValueError:
        archive_rel = str(archive_path)

    result = AppliedConfig(
        experiment=experiment_name,
        saved=original_path,
        archived=archive_path,
        updated=tuple(updated_metrics),
        preserved=tuple(preserved_metrics),
        experiment_fields=tuple(experiment_fields),
        orphaned=tuple(orphaned),
    )

    header = _header(
        experiment_name,
        archive_rel,
        stamp,
        updated_metrics,
        preserved_metrics,
        experiment_fields,
        result.orphan_warning,
    )
    original_path.write_bytes(header.encode("utf-8") + b"\n" + _reemit_yaml(raw, original_bytes))
    return result
