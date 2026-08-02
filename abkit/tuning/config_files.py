"""Create / update / delete an experiment YAML — the dashboard's CRUD seam (UI-1).

The mutation half of ``abk dashboard``'s editor, and the second write seam in
``abkit/tuning/`` after :mod:`abkit.tuning.config_writer` (explore's Apply).
The two are deliberately different shapes and share only their filesystem
primitives (:func:`archive_config_text`, :func:`atomic_write_bytes`, which
``config_writer`` imports from here so both write the same archive):

* **Apply merges a structured edit** — the cockpit sends method params, the
  writer merges them into the parsed document and RE-EMITS it, so comments die
  and the archive is the recovery (D4).
* **This module round-trips TEXT.** The operator edits the raw YAML in the
  browser and exactly those bytes land on disk, comments and layout intact
  (normalized only to end with a newline). That is the donor's
  ``detectkit/ui/metric_files.py`` discipline, and it is why the editor is not
  built on ``apply_tuned_config``: a save that silently reformatted the file it
  was just shown would be a worse editor than none.

Order is **validate → archive → write** (the house rule): a config that would
not survive ``abk run --steps validate`` never lands, and the previous file is
always recoverable byte-verbatim from ``<dir>/.history/<experiment>/``, which
discovery excludes so archives are never picked up as live configs.

Validation is BOTH levels, because level 1 alone accepts a config the pipeline
would refuse: :class:`~abkit.config.experiment_config.ExperimentConfig` checks
the file against itself, while
:func:`~abkit.config.validator.validate_experiment_level2` is the §8 matrix —
reference integrity (a comparison naming a metric that does not exist), the
CUPED covariate rules, the cadence/looks gates over the real grid, and the
no-DB SQL render smoke. Its *errors* refuse the write; its *warnings* ride back
in the reply, exactly as ``abk run --steps validate`` prints them. Level 2 —
and only level 2 — is overridable with ``force``, because it is a statement
about the whole project rather than about this file, and an editor that cannot
save the first of three files an operator is fixing is an editor nobody can use
(see :func:`check_level2`).

Nothing here touches the database. Rows keyed by a deleted or renamed
experiment stay in the ``_ab_*`` tables until ``abk clean`` prunes them — said
out loud in the delete reply, never done silently: ``clean`` deletes data and
deserves explicit intent (the ``config_writer`` precedent).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.config.validator import (
    discover_config_files,
    validate_experiment_level2,
)

#: Filename-unsafe shapes for a name-derived stem. The experiment-name
#: validator already refuses everything but alphanumerics, ``_`` and ``-``, so
#: none of these can arrive through a validated config today — the guard exists
#: because the stem is the ONE place a config field becomes a filesystem path,
#: and it is checked (and tested) as a function of its input rather than
#: assumed from a validator two modules away.
_UNSAFE_STEM_PARTS = ("/", "\\", "\x00")


@dataclass(frozen=True)
class ConfigWrite:
    """Result of one create/update: the file written, plus what it cost.

    ``archived`` is ``None`` for a create (there was nothing to preserve) and
    the verbatim copy of the PREVIOUS text otherwise. ``renamed_from`` is set
    when the saved text changed the experiment's ``name:`` — the file keeps its
    path, so the caller (and the operator) must be told that the row's identity
    moved, and that the old name's persisted rows are now orphaned.
    """

    path: Path
    config: ExperimentConfig
    #: The digest OF THE BYTES JUST WRITTEN — never re-read from disk. A
    #: re-read would hash whatever is there NOW, so a concurrent writer's text
    #: would be handed back to this editor as the token for ITS OWN buffer, and
    #: the next save would pass the digest check while clobbering that writer.
    digest: str = ""
    archived: Path | None = None
    renamed_from: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass
class _NameIndex:
    """Every live name in the project's ONE namespace, by file.

    Experiments and metrics share a namespace (``cli-and-dx.md`` §1), so a save
    must check both — an experiment named like a metric breaks two-level
    selection and is refused by ``validate_project_configs`` at the next run,
    i.e. long after the editor said "saved".
    """

    experiments: dict[Path, str] = field(default_factory=dict)
    metrics: dict[Path, str] = field(default_factory=dict)


def text_digest(text: str) -> str:
    """Stable digest of a config file's text — the editor's concurrency token.

    ``GET /api/experiment-source`` hands it out with the text; the editor echoes
    it back on save, and :func:`update_experiment_file` refuses when the on-disk
    text no longer matches. Two writers make that ordinary rather than exotic
    here: a second browser tab, and ``abk explore``'s Apply — which the
    dashboard itself can spawn, on the very experiment being edited.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp(now: datetime | None = None) -> str:
    """UTC filesystem-safe timestamp (``20260802T101530Z``) — the archive key."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _archive_key(name: str, config_path: Path) -> str:
    """A directory name for the archive that cannot leave ``<dir>/.history/``.

    Neither candidate is trusted: *name* is whatever the file's ``name:`` holds
    right now (``lenient_name`` returns any non-empty string), and the file stem
    is a filename the operator chose. An ABSOLUTE name is the sharp edge —
    pathlib resets a join on one, so ``name: /tmp/x`` would ``mkdir`` and write
    OUTSIDE the project entirely (reproduced through the live save route).
    Falling back rather than refusing is deliberate: the archive is the recovery
    copy, and a worse key beats not preserving the file at all.
    """
    for candidate in (name, config_path.stem):
        try:
            return _safe_stem(candidate)
        except ValueError:
            continue
    return "config"


def archive_config_text(
    *,
    config_path: Path,
    name: str,
    original: bytes,
    now: datetime | None = None,
    suffix: str = "",
) -> Path:
    """Preserve *original* under ``<dir>/.history/<name>/`` and return the path.

    THE archive seam — ``config_writer``'s Apply and this module's
    update/delete all land in the same tree, keyed by the config's own name, so
    an operator recovering a file does not have to know which surface wrote it.
    The bytes are written verbatim (never re-emitted), and a same-second second
    write gets a ``-2`` suffix rather than clobbering its predecessor.

    *suffix* distinguishes a delete's tombstone (``-deleted``) from an ordinary
    pre-write snapshot; it rides in the FILENAME, so a directory listing sorts
    by time and still says which archive is the last live version.
    """
    # The key is read off DISK (`lenient_name`), so it is whatever string the
    # file's `name:` happens to hold — not a validated config field. An absolute
    # one resets a pathlib join, so `name: /tmp/x` would mkdir and write OUTSIDE
    # the project (reproduced through the live save route). Anything unusable
    # falls back to the file's own stem: the archive is a recovery copy, and a
    # slightly worse key beats refusing to preserve the file at all.
    key = _archive_key(name, config_path)
    archive_dir = config_path.parent / ".history" / key
    archive_dir.mkdir(parents=True, exist_ok=True)
    at = stamp(now)
    archive_path = archive_dir / f"{key}-{at}{suffix}.yml"
    ordinal = 1
    while archive_path.exists():  # rapid same-second writes must not clobber
        ordinal += 1
        archive_path = archive_dir / f"{key}-{at}{suffix}-{ordinal}.yml"
    archive_path.write_bytes(original)
    return archive_path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """temp + ``os.replace`` in the target's own directory (same filesystem)
    so a mid-write failure (ENOSPC, SIGKILL, power loss) can never truncate
    the user's live config — "a broken config never lands" applies to the
    filesystem, not just the validator."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def normalized(text: str) -> str:
    """The text as it lands on disk: exactly what was typed, plus a final newline."""
    return text if text.endswith("\n") else text + "\n"


def parse_experiment_text(text: str) -> tuple[ExperimentConfig, dict[str, Any]]:
    """Validate raw YAML text as an experiment config (level 1).

    Accepts both the flat form and the nested ``experiment: {...}`` form
    ``ExperimentConfig.from_yaml_file`` supports. Returns the validated config
    and the raw body mapping (what the file actually SETS, without pydantic
    defaults — the shape a future form editor seeds from).

    Every failure — YAML syntax, a non-mapping document, a pydantic
    ValidationError, the renamed-window-field refusal — is raised as a
    ``ValueError`` whose message is fit for the editor's error pane.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ValueError("an experiment config must be a non-empty YAML mapping")
    body = raw["experiment"] if isinstance(raw.get("experiment"), dict) else raw
    if not isinstance(body, dict) or not body:
        raise ValueError("an experiment config must be a non-empty YAML mapping")
    try:
        config = ExperimentConfig.model_validate(body)
    except Exception as exc:
        raise ValueError(f"invalid experiment config: {exc}") from exc
    return config, body


def lenient_name(text: str) -> str | None:
    """The ``name:`` of an experiment YAML text, or ``None`` when unparseable.

    Lenient by design: a broken sibling file must not be able to block a save
    (project validation surfaces it loudly at the next run), and the answer is
    only ever used for a uniqueness check and as an archive-directory key.
    """
    try:
        raw = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — an unparseable file just doesn't participate
        return None
    if not isinstance(raw, dict):
        return None
    body = raw["experiment"] if isinstance(raw.get("experiment"), dict) else raw
    if not isinstance(body, dict):
        return None
    name = body.get("name")
    return name if isinstance(name, str) and name else None


def _lenient_file_name(path: Path) -> str | None:
    try:
        return lenient_name(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def load_metric_configs(project_root: Path, project: ProjectConfig) -> dict[str, MetricConfig]:
    """Every metric config that parses, by name — the level-2 reference table.

    Lenient on purpose, and read FRESH on every save rather than taken from the
    dashboard's boot snapshot: an operator who added a metric while the cockpit
    was running must be able to reference it, and a metric that is broken must
    not block an unrelated experiment's save. A metric that fails to parse is
    simply absent, so a comparison naming it fails the reference check with the
    same message it would get for a typo.
    """
    metrics_dir = project_root / project.paths.metrics
    if not metrics_dir.exists():
        return {}
    found: dict[str, MetricConfig] = {}
    for path in discover_config_files(metrics_dir):
        try:
            config = MetricConfig.from_yaml_file(path)
        except Exception:  # noqa: BLE001 — a broken metric is absent, never fatal
            continue
        found.setdefault(config.name, config)
    return found


def _name_index(project_root: Path, project: ProjectConfig) -> _NameIndex:
    """Every live experiment/metric name in the project, keyed by file."""
    index = _NameIndex()
    experiments_dir = project_root / project.paths.experiments
    metrics_dir = project_root / project.paths.metrics
    if experiments_dir.exists():
        for path in discover_config_files(experiments_dir):
            name = _lenient_file_name(path)
            if name is not None:
                index.experiments[path] = name
    if metrics_dir.exists():
        for path in discover_config_files(metrics_dir):
            name = _lenient_file_name(path)
            if name is not None:
                index.metrics[path] = name
    return index


def _ensure_unique_name(
    project_root: Path, project: ProjectConfig, name: str, exclude: Path | None
) -> None:
    """Refuse a name another live config already claims (ONE namespace, §1).

    Checked over the whole project rather than the dashboard's selection: a
    duplicate corrupts the shared ``_ab_*`` tables and breaks selection
    regardless of what the current page happens to show, and the operator would
    only find out at the next ``abk run``.
    """
    excluded = exclude.resolve() if exclude is not None else None
    index = _name_index(project_root, project)
    for path, other in index.experiments.items():
        if excluded is not None and path.resolve() == excluded:
            continue
        if other == name:
            raise ValueError(
                f"experiment name '{name}' is already used by {_rel(path, project_root)} — "
                "experiment names must be unique across the project"
            )
    for path, other in index.metrics.items():
        if other == name:
            raise ValueError(
                f"'{name}' is already the name of the metric in {_rel(path, project_root)} — "
                "experiment and metric names share ONE namespace (two-level selection "
                "would be ambiguous), so rename one of them"
            )


def _safe_stem(name: str) -> str:
    """*name* as a file stem, or ``ValueError``.

    The one place a config field becomes a filesystem path. Kept as an explicit
    function of its input (rather than trusting
    ``ExperimentConfig.validate_name`` two modules away) because the two rules
    answer different questions: the validator asks whether a NAME is legal, this
    asks whether it can be a FILE. A hidden stem would also be invisible to
    discovery, so the file would be written and never seen again.
    """
    if not name or name != name.strip():
        raise ValueError(f"'{name}' cannot be a file name (empty or padded with whitespace)")
    if name.startswith("."):
        raise ValueError(
            f"'{name}' cannot be a file name: a leading dot makes the file hidden, "
            "and discovery skips hidden paths"
        )
    for part in _UNSAFE_STEM_PARTS:
        if part in name:
            raise ValueError(f"'{name}' cannot be a file name: it contains {part!r}")
    if name in (os.curdir, os.pardir):
        raise ValueError(f"'{name}' cannot be a file name")
    return name


def _safe_folder(base_dir: Path, folder: str) -> Path:
    """``<experiments>/<folder>`` with every component charset-checked.

    No ``..``, no absolute paths, no hidden components — the target has to be a
    directory discovery will actually look in. An ABSOLUTE folder is refused
    rather than quietly re-read as a relative one (the donor strips the leading
    slash): ``/etc`` would land in ``experiments/etc``, which is safe and is not
    what was asked for, and silently writing somewhere other than where the
    caller said is the failure mode this whole module exists to avoid. A
    trailing slash is just a typo and is stripped.
    """
    cleaned = folder.strip().rstrip("/")
    if not cleaned:
        return base_dir
    parts: list[str] = []
    for part in cleaned.split("/"):
        if not part or part in (os.curdir, os.pardir) or part.startswith("."):
            raise ValueError(
                f"folder '{folder}' is not usable — no '..', no hidden components, "
                "and every part must be a real directory name"
            )
        parts.append(_safe_stem(part))
    return base_dir.joinpath(*parts)


def guard_editable(path: Path, base_dir: Path) -> None:
    """Only a live YAML under the experiments root is editable — never an archive.

    The dashboard never lets a client name a path (every route addresses an
    experiment by NAME and takes the path from its own index), so this is the
    belt to that braces: it is what keeps a future caller — or a project whose
    ``paths.experiments`` moved under the running cockpit — from writing
    outside the tree, and it refuses ``.history`` explicitly, because
    overwriting an archive would destroy the one recovery this module promises.
    """
    try:
        parts = path.resolve().relative_to(base_dir.resolve()).parts
    except ValueError:
        raise ValueError(f"not an experiment file under {base_dir}: {path}") from None
    if not parts:
        raise ValueError(f"not an experiment file under {base_dir}: {path}")
    if any(part.startswith(".") for part in parts):
        raise ValueError(f"refusing to edit an archived or hidden config file: {path}")


def check_level2(
    *,
    config: ExperimentConfig,
    experiment_path: Path,
    project_root: Path,
    project: ProjectConfig,
    force: bool = False,
) -> tuple[str, ...]:
    """The §8 matrix for one experiment: errors refuse, warnings are returned.

    The same battery ``abk run --steps validate`` runs, over the SAME grid
    enumeration the planner uses — so "it saved" means "it would have run".

    *force* downgrades the errors to warnings, and the split between the two
    validation levels is the reason it exists at all. **Level 1 is never
    forceable**: a file that is not an ``ExperimentConfig`` cannot be served as
    a row, so accepting it would break the page that is showing it. Level 2 is
    a statement about the whole PROJECT — a metric that does not exist yet, an
    SQL file still being written — and an editor that refuses to save until the
    project is coherent is unusable in exactly the situation an operator opens
    it for: fixing an incoherent project, one file at a time. So the default
    refuses, the client shows the errors, and the operator can insist; the next
    ``abk run`` refuses just as loudly, which is what keeps "forced" from
    meaning "forgotten".
    """
    report = validate_experiment_level2(
        config,
        load_metric_configs(project_root, project),
        project,
        project_root,
        experiment_path,
    )
    if report.ok:
        return tuple(report.warnings)
    if not force:
        raise ValueError(
            "the config is valid YAML but not valid for this project:\n  - "
            + "\n  - ".join(report.errors)
            + "\n(save it anyway to keep the text — `abk run` will refuse it "
            "until these are fixed)"
        )
    return (
        *(f"SAVED WITH AN ERROR — `abk run` will refuse this: {e}" for e in report.errors),
        *report.warnings,
    )


def create_experiment_file(
    *,
    project_root: Path,
    project: ProjectConfig,
    text: str,
    folder: str = "",
    force: bool = False,
) -> ConfigWrite:
    """Validate *text* and write it as a NEW ``<experiments>/[<folder>/]<name>.yml``.

    The file name is derived from the config's own ``name:`` rather than asked
    for separately — one identity, and the same convention ``abk init``
    scaffolds. Raises ``ValueError`` (writing nothing) on invalid YAML or
    config, a name already claimed anywhere in the project, an unusable folder,
    or a target file that already exists.
    """
    config, _body = parse_experiment_text(text)
    base_dir = project_root / project.paths.experiments
    target_dir = _safe_folder(base_dir, folder)
    path = target_dir / f"{_safe_stem(config.name)}.yml"
    if path.exists():
        raise ValueError(
            f"{_rel(path, project_root)} already exists — open it from the list to edit it"
        )
    _ensure_unique_name(project_root, project, config.name, exclude=None)
    warnings = check_level2(
        config=config,
        experiment_path=path,
        project_root=project_root,
        project=project,
        force=force,
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    written = normalized(text)
    atomic_write_bytes(path, written.encode("utf-8"))
    return ConfigWrite(path=path, config=config, digest=text_digest(written), warnings=warnings)


def update_experiment_file(
    *,
    project_root: Path,
    project: ProjectConfig,
    path: Path,
    text: str,
    expected_digest: str | None = None,
    force: bool = False,
) -> ConfigWrite:
    """Validate *text*, archive the previous file verbatim, then overwrite in place.

    A rename (the YAML's ``name:`` changed) is allowed and reported back as
    ``renamed_from``: the file keeps its path, uniqueness is re-checked against
    every OTHER live config, and the archive is keyed by the **old** name — it
    is the old config being preserved.

    *expected_digest* is the :func:`text_digest` of the text the editor was
    opened with. When it no longer matches what is on disk the write is refused
    and nothing is touched: an ``abk explore`` Apply or a second tab saved in
    between, and overwriting it would silently lose that change.
    """
    base_dir = project_root / project.paths.experiments
    guard_editable(path, base_dir)
    # The digest is checked BEFORE the text is validated, and the order is the
    # message: when the buffer is both stale and broken, "fix your YAML" is
    # advice about text that is going to be thrown away — the operator has to
    # reopen the file either way, and only then does their edit mean anything.
    try:
        original = path.read_bytes()
    except FileNotFoundError:
        raise ValueError(
            f"{_rel(path, project_root)} is gone — it was moved or deleted after this "
            "editor was opened; nothing was written"
        ) from None
    original_text = original.decode("utf-8", errors="replace")
    if expected_digest is not None and text_digest(original_text) != expected_digest:
        raise ValueError(
            "the file changed on disk after this editor was opened (an `abk explore` "
            "Apply, or another tab?) — nothing was written; reopen the experiment to "
            "load the latest version"
        )
    config, _body = parse_experiment_text(text)
    _ensure_unique_name(project_root, project, config.name, exclude=path)
    warnings = check_level2(
        config=config,
        experiment_path=path,
        project_root=project_root,
        project=project,
        force=force,
    )
    old_name = lenient_name(original_text) or path.stem
    archived = archive_config_text(config_path=path, name=old_name, original=original)
    written = normalized(text)
    atomic_write_bytes(path, written.encode("utf-8"))
    return ConfigWrite(
        path=path,
        config=config,
        digest=text_digest(written),
        archived=archived,
        renamed_from=old_name if old_name != config.name else None,
        warnings=warnings,
    )


def delete_experiment_file(
    *,
    project_root: Path,
    project: ProjectConfig,
    path: Path,
    expected_digest: str | None = None,
) -> Path:
    """Archive the file verbatim (``…-deleted.yml``), remove it, return the archive.

    Only the YAML goes: the experiment's rows in the ``_ab_*`` tables stay until
    ``abk clean --orphaned-experiments`` prunes them, and the archived copy makes
    the delete reversible by hand. Both facts belong in the caller's reply — a
    delete that quietly stranded a warehouse series would be the silent half of
    a destructive button.
    """
    base_dir = project_root / project.paths.experiments
    guard_editable(path, base_dir)
    try:
        original = path.read_bytes()
    except FileNotFoundError:
        raise ValueError(
            f"{_rel(path, project_root)} is already gone — nothing was deleted"
        ) from None
    original_text = original.decode("utf-8", errors="replace")
    if expected_digest is not None and text_digest(original_text) != expected_digest:
        raise ValueError(
            "the file changed on disk after this editor was opened — nothing was "
            "deleted; reopen the experiment to see the current version"
        )
    name = lenient_name(original_text) or path.stem
    archived = archive_config_text(
        config_path=path, name=name, original=original, suffix="-deleted"
    )
    path.unlink()
    return archived


def _rel(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()
