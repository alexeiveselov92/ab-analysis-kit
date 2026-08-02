"""UI-1 tests: the dashboard's CRUD seam (``abkit/tuning/config_files.py``).

The route-level contracts live in ``test_dashboard_server.py::TestEditorRoutes``
(real HTTP, the house pattern). This suite covers the module on its own, where
the hostile inputs are cheap to express: a name that cannot be a file, a folder
that tries to leave the tree, an archive asked to overwrite itself, a digest
race, and the level-1/level-2 split that decides what ``force`` may override.

The donor is ``detectkit/ui/metric_files.py``; the shapes ported verbatim are
validate-before-write, the verbatim archive, the lenient name read, and the
optimistic-concurrency digest. What is abkit's own: BOTH validation levels
(detectkit has no §8 matrix), the ONE shared namespace with metrics, and the
shared archive/atomic-write primitives ``config_writer`` now imports from here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from abkit.config import ProjectConfig
from abkit.tuning import config_files

NOW = datetime(2026, 8, 2, 10, 15, 30)

PROJECT = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})

EXPERIMENT = """\
# a comment that must survive the round trip
name: {name}
start_ts: 2026-01-01
horizon_ts: 2026-01-15
unit_key: user_id
assignment:
  query: SELECT user_id, variant, exposure_ts FROM assignments
  variants: [control, treatment]
  expected_split: {{control: 0.5, treatment: 0.5}}
comparisons:
  - metric: {metric}
    is_main_metric: true
    method: {{name: t-test}}
"""

METRIC = """\
name: revenue
type: sample
columns:
  variant: variant
  value: value
query: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT _abk_exposures._abk_variant AS variant, t.user_id AS user_id, t.amount AS value
  FROM events t
  {{ ab.exposed_units() }}
"""


def experiment_text(name: str = "exp_one", metric: str = "revenue") -> str:
    return EXPERIMENT.format(name=name, metric=metric)


@pytest.fixture
def project(tmp_path) -> Path:
    """A project that passes `abk run --steps validate`."""
    (tmp_path / "experiments").mkdir()
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "revenue.yml").write_text(METRIC, encoding="utf-8")
    (tmp_path / "experiments" / "exp_one.yml").write_text(experiment_text(), encoding="utf-8")
    return tmp_path


def update(project_root: Path, name: str = "exp_one", **kwargs):
    kwargs.setdefault("path", project_root / "experiments" / f"{name}.yml")
    return config_files.update_experiment_file(project_root=project_root, project=PROJECT, **kwargs)


def create(project_root: Path, **kwargs):
    return config_files.create_experiment_file(project_root=project_root, project=PROJECT, **kwargs)


class TestParse:
    def test_the_flat_and_nested_forms_both_parse(self):
        flat, body = config_files.parse_experiment_text(experiment_text())
        nested_text = "experiment:\n" + "\n".join(
            f"  {line}" for line in experiment_text().splitlines()
        )
        nested, nested_body = config_files.parse_experiment_text(nested_text)
        assert flat.name == nested.name == "exp_one"
        # the raw body is what the FILE sets — no pydantic defaults
        assert set(body) == set(nested_body)
        assert "correction" not in body

    @pytest.mark.parametrize(
        ("text", "says"),
        [
            ("name: [unclosed", "invalid YAML"),
            ("", "non-empty YAML mapping"),
            ("- a\n- b\n", "non-empty YAML mapping"),
            ("experiment: {}\n", "non-empty YAML mapping"),
            ("name: exp_one\n", "invalid experiment config"),
        ],
    )
    def test_every_failure_is_a_valueerror_fit_for_an_error_pane(self, text, says):
        with pytest.raises(ValueError, match=says):
            config_files.parse_experiment_text(text)

    def test_a_renamed_window_field_still_names_its_replacement(self):
        """The m10 rename must reach the editor's error pane, not a traceback."""
        with pytest.raises(ValueError) as exc:
            config_files.parse_experiment_text(
                experiment_text().replace("start_ts: 2026-01-01", "start_date: 2026-01-01")
            )
        assert "start_ts" in str(exc.value)

    def test_lenient_name_never_raises(self):
        assert config_files.lenient_name(experiment_text()) == "exp_one"
        assert config_files.lenient_name("experiment:\n  name: nested\n") == "nested"
        assert config_files.lenient_name("name: [") is None
        assert config_files.lenient_name("- a\n") is None
        assert config_files.lenient_name("name: 7\n") is None  # not a string


class TestSafeNames:
    """The one place a config field becomes a filesystem path."""

    @pytest.mark.parametrize(
        "name", ["a/b", "a\\b", ".hidden", "", "  ", " padded", "..", ".", "a\x00b"]
    )
    def test_an_unusable_stem_is_refused(self, name):
        with pytest.raises(ValueError):
            config_files._safe_stem(name)

    @pytest.mark.parametrize("name", ["exp_one", "exp-1", "a", "Ünïcode"])
    def test_a_usable_stem_is_returned_unchanged(self, name):
        assert config_files._safe_stem(name) == name

    @pytest.mark.parametrize("folder", ["..", "a/../b", "/abs", ".hidden", "a/.b", "a//b"])
    def test_a_folder_cannot_leave_or_hide(self, tmp_path, folder):
        with pytest.raises(ValueError):
            config_files._safe_folder(tmp_path, folder)

    def test_an_absolute_folder_is_refused_rather_than_reread_as_relative(self, tmp_path):
        """The donor strips the slash; `/etc` would land in `experiments/etc`."""
        with pytest.raises(ValueError, match="not usable"):
            config_files._safe_folder(tmp_path, "/etc")

    def test_a_trailing_slash_is_only_a_typo(self, tmp_path):
        assert config_files._safe_folder(tmp_path, "growth/") == tmp_path / "growth"
        assert config_files._safe_folder(tmp_path, "  ") == tmp_path


class TestGuardEditable:
    def test_an_archive_is_never_editable(self, project):
        base = project / "experiments"
        with pytest.raises(ValueError, match="archived or hidden"):
            config_files.guard_editable(base / ".history" / "exp_one" / "x.yml", base)

    def test_a_path_outside_the_tree_is_refused(self, project, tmp_path):
        with pytest.raises(ValueError, match="not an experiment file"):
            config_files.guard_editable(tmp_path / "elsewhere.yml", project / "experiments")

    def test_the_base_directory_itself_is_not_a_file(self, project):
        base = project / "experiments"
        with pytest.raises(ValueError, match="not an experiment file"):
            config_files.guard_editable(base, base)


class TestArchive:
    def test_it_preserves_the_bytes_verbatim(self, tmp_path):
        path = tmp_path / "exp.yml"
        original = b"# comment\nname: exp\n"
        path.write_bytes(original)
        archived = config_files.archive_config_text(
            config_path=path, name="exp", original=original, now=NOW
        )
        assert archived == tmp_path / ".history" / "exp" / "exp-20260802T101530Z.yml"
        assert archived.read_bytes() == original

    def test_a_same_second_second_archive_does_not_clobber(self, tmp_path):
        path = tmp_path / "exp.yml"
        first = config_files.archive_config_text(
            config_path=path, name="exp", original=b"one", now=NOW
        )
        second = config_files.archive_config_text(
            config_path=path, name="exp", original=b"two", now=NOW
        )
        assert first != second
        assert (first.read_bytes(), second.read_bytes()) == (b"one", b"two")

    def test_the_delete_tombstone_is_named_in_the_filename(self, tmp_path):
        archived = config_files.archive_config_text(
            config_path=tmp_path / "exp.yml", name="exp", original=b"x", now=NOW, suffix="-deleted"
        )
        assert archived.name == "exp-20260802T101530Z-deleted.yml"


class TestArchiveKeyCannotEscape:
    """The archive key is read off DISK, so it is not a validated field."""

    def test_an_absolute_name_in_the_file_does_not_write_outside_the_project(
        self, project, tmp_path
    ):
        """`pathlib` resets a join on an absolute component — `name: /tmp/x`
        would have made the archive `mkdir` and write anywhere at all."""
        outside = tmp_path.parent / "OUTSIDE_THE_PROJECT"
        path = project / "experiments" / "exp_one.yml"
        path.write_text(
            experiment_text().replace("name: exp_one", f"name: {outside}/pwned"),
            encoding="utf-8",
        )
        update(project, text=experiment_text())
        assert not outside.exists()
        assert (path.parent / ".history").is_dir()
        assert all(
            (path.parent / ".history").resolve() in p.resolve().parents
            for p in (path.parent / ".history").rglob("*.yml")
        )

    @pytest.mark.parametrize("name", ["/abs/x", "../up", "a/b", ".hidden", ""])
    def test_the_key_falls_back_rather_than_refusing_to_preserve_the_file(self, tmp_path, name):
        key = config_files._archive_key(name, tmp_path / "the_stem.yml")
        assert key == "the_stem"

    def test_it_falls_back_again_when_the_stem_is_unusable_too(self, tmp_path):
        assert config_files._archive_key("/abs", tmp_path / ".hidden.yml") == "config"


class TestUpdate:
    def test_the_text_lands_verbatim_and_the_previous_is_archived(self, project):
        path = project / "experiments" / "exp_one.yml"
        before = path.read_text(encoding="utf-8")
        edited = before.replace("unit_key: user_id", "unit_key: user_id  # tuned by hand")

        written = update(project, text=edited)

        assert path.read_text(encoding="utf-8") == edited
        assert written.archived is not None
        assert written.archived.read_text(encoding="utf-8") == before
        assert written.renamed_from is None

    def test_the_returned_digest_is_of_the_bytes_just_written(self, project):
        """Never a re-read: a re-read hands this editor a token certifying
        whatever is on disk NOW, i.e. possibly another writer's text."""
        written = update(project, text=experiment_text().replace("2026-01-15", "2026-01-20"))
        on_disk = written.path.read_text(encoding="utf-8")
        assert written.digest == config_files.text_digest(on_disk)

    def test_a_missing_trailing_newline_is_the_only_normalization(self, project):
        text = experiment_text().rstrip("\n")
        update(project, text=text)
        on_disk = (project / "experiments" / "exp_one.yml").read_text(encoding="utf-8")
        assert on_disk == text + "\n"

    def test_a_stale_digest_writes_nothing(self, project):
        path = project / "experiments" / "exp_one.yml"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="changed on disk"):
            update(project, text=experiment_text(), expected_digest="0" * 64)
        assert path.read_bytes() == before
        assert not (path.parent / ".history").exists()  # not even archived

    def test_a_stale_digest_is_reported_before_a_broken_buffer(self, project):
        """Both wrong: the operator has to reopen anyway, so say THAT."""
        with pytest.raises(ValueError, match="changed on disk"):
            update(project, text="name: [", expected_digest="0" * 64)

    def test_the_current_digest_is_accepted(self, project):
        path = project / "experiments" / "exp_one.yml"
        digest = config_files.text_digest(path.read_text(encoding="utf-8"))
        update(project, text=experiment_text(), expected_digest=digest)

    def test_a_rename_is_allowed_and_archives_under_the_OLD_name(self, project):
        written = update(project, text=experiment_text(name="exp_renamed"))
        assert written.renamed_from == "exp_one"
        assert written.config.name == "exp_renamed"
        assert written.archived.parent.name == "exp_one"
        # the FILE keeps its path — only the config's identity moved
        assert written.path == project / "experiments" / "exp_one.yml"

    def test_a_rename_onto_another_live_name_is_refused(self, project):
        (project / "experiments" / "exp_two.yml").write_text(
            experiment_text(name="exp_two"), encoding="utf-8"
        )
        path = project / "experiments" / "exp_one.yml"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="already used by"):
            update(project, text=experiment_text(name="exp_two"))
        assert path.read_bytes() == before

    def test_renaming_a_file_to_its_own_name_is_not_a_collision(self, project):
        """The uniqueness scan must exclude the file being written."""
        update(project, text=experiment_text().replace("2026-01-15", "2026-01-20"))

    def test_a_vanished_file_is_a_valueerror_not_a_traceback(self, project):
        path = project / "experiments" / "exp_one.yml"
        path.unlink()
        with pytest.raises(ValueError, match="is gone"):
            update(project, text=experiment_text())

    def test_an_unparseable_sibling_cannot_block_a_save(self, project):
        """Lenient by design: a broken file just does not participate."""
        (project / "experiments" / "broken.yml").write_text("name: [", encoding="utf-8")
        update(project, text=experiment_text().replace("2026-01-15", "2026-01-20"))

    def test_a_broken_metric_file_does_not_block_an_unrelated_save(self, project):
        (project / "metrics" / "broken.yml").write_text("type: [", encoding="utf-8")
        update(project, text=experiment_text().replace("2026-01-15", "2026-01-20"))


class TestValidationLevels:
    def test_level_2_refuses_a_metric_that_does_not_exist(self, project):
        path = project / "experiments" / "exp_one.yml"
        before = path.read_bytes()
        with pytest.raises(ValueError, match="no metric named 'nope'"):
            update(project, text=experiment_text(metric="nope"))
        assert path.read_bytes() == before

    def test_force_downgrades_a_level_2_error_to_a_loud_warning(self, project):
        written = update(project, text=experiment_text(metric="nope"), force=True)
        assert any("SAVED WITH AN ERROR" in w for w in written.warnings)
        assert "no metric named 'nope'" in " ".join(written.warnings)

    def test_force_cannot_get_a_level_1_failure_past(self, project):
        with pytest.raises(ValueError, match="invalid experiment config"):
            update(project, text="name: exp_one\n", force=True)

    def test_level_2_warnings_ride_back_without_blocking(self, project):
        """A peeking-risk cadence is a warning at run time; same here."""
        dense = experiment_text().replace(
            "unit_key: user_id", "unit_key: user_id\ncadence: 1h\ndata_lag: 0", 1
        )
        written = update(project, text=dense)
        assert written.warnings
        assert any("peeking risk" in w for w in written.warnings)

    def test_the_metric_library_is_re_read_per_save(self, project):
        """A metric added while the cockpit ran must be referenceable."""
        (project / "metrics" / "later.yml").write_text(
            METRIC.replace("name: revenue", "name: later"), encoding="utf-8"
        )
        update(project, text=experiment_text(metric="later"))


class TestCreate:
    def test_it_writes_a_file_named_after_the_config(self, project):
        written = create(project, text=experiment_text(name="exp_new"))
        assert written.path == project / "experiments" / "exp_new.yml"
        assert written.archived is None
        assert written.path.read_text(encoding="utf-8") == experiment_text(name="exp_new")

    def test_a_folder_is_created_under_the_experiments_root(self, project):
        written = create(project, text=experiment_text(name="exp_new"), folder="growth/q3")
        assert written.path == project / "experiments" / "growth" / "q3" / "exp_new.yml"

    def test_an_existing_file_is_never_overwritten(self, project):
        with pytest.raises(ValueError, match="already exists"):
            create(project, text=experiment_text(name="exp_one"))

    def test_a_name_used_elsewhere_is_refused_before_anything_is_written(self, project):
        with pytest.raises(ValueError, match="already used by"):
            create(project, text=experiment_text(name="exp_one"), folder="growth")
        assert not (project / "experiments" / "growth").exists()

    def test_the_metric_namespace_is_the_same_namespace(self, project):
        with pytest.raises(ValueError, match="share ONE namespace"):
            create(project, text=experiment_text(name="revenue"))

    def test_a_hidden_archive_is_not_a_name_collision(self, project):
        """`.history` is excluded from discovery, so its copies never collide."""
        config_files.archive_config_text(
            config_path=project / "experiments" / "exp_one.yml",
            name="exp_ghost",
            original=experiment_text(name="exp_ghost").encode(),
            now=NOW,
        )
        create(project, text=experiment_text(name="exp_ghost"))


class TestDelete:
    def test_it_archives_then_unlinks(self, project):
        path = project / "experiments" / "exp_one.yml"
        before = path.read_bytes()
        archived = config_files.delete_experiment_file(
            project_root=project, project=PROJECT, path=path
        )
        assert not path.exists()
        assert archived.read_bytes() == before
        assert archived.name.endswith("-deleted.yml")

    def test_a_stale_digest_deletes_nothing(self, project):
        path = project / "experiments" / "exp_one.yml"
        with pytest.raises(ValueError, match="changed on disk"):
            config_files.delete_experiment_file(
                project_root=project, project=PROJECT, path=path, expected_digest="0" * 64
            )
        assert path.exists()

    def test_an_archive_cannot_be_deleted_through_this_route(self, project):
        archived = config_files.archive_config_text(
            config_path=project / "experiments" / "exp_one.yml",
            name="exp_one",
            original=b"x",
            now=NOW,
        )
        with pytest.raises(ValueError, match="archived or hidden"):
            config_files.delete_experiment_file(
                project_root=project, project=PROJECT, path=archived
            )
        assert archived.exists()


class TestArchivesAreNotConfigs:
    def test_discovery_never_picks_an_archive_up(self, project):
        from abkit.config.validator import discover_config_files

        update(project, text=experiment_text().replace("2026-01-15", "2026-01-20"))
        found = discover_config_files(project / "experiments")
        assert found == [project / "experiments" / "exp_one.yml"]
