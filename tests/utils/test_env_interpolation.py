"""Tests for env variable interpolation utility."""

from abkit.utils.env_interpolation import interpolate_env_vars


class TestInterpolateEnvVars:
    def test_shell_style(self, monkeypatch):
        monkeypatch.setenv("ABKIT_TEST_HOST", "ch.internal")
        assert interpolate_env_vars("${ABKIT_TEST_HOST}") == "ch.internal"

    def test_dbt_style(self, monkeypatch):
        monkeypatch.setenv("ABKIT_TEST_PASSWORD", "s3cret")
        result = interpolate_env_vars("{{ env_var('ABKIT_TEST_PASSWORD') }}")
        assert result == "s3cret"

    def test_unresolved_kept_literally(self, monkeypatch):
        monkeypatch.delenv("ABKIT_NOT_SET", raising=False)
        assert interpolate_env_vars("${ABKIT_NOT_SET}") == "${ABKIT_NOT_SET}"
        assert interpolate_env_vars("{{ env_var('ABKIT_NOT_SET') }}") == (
            "{{ env_var('ABKIT_NOT_SET') }}"
        )

    def test_mixed_inline(self, monkeypatch):
        monkeypatch.setenv("HOOK_TOKEN", "abc")
        result = interpolate_env_vars("Bearer ${HOOK_TOKEN}")
        assert result == "Bearer abc"

    def test_recursive_dict(self, monkeypatch):
        monkeypatch.setenv("CH_HOST", "10.0.0.1")
        monkeypatch.setenv("CH_PASS", "pw")
        config = {
            "type": "clickhouse",
            "host": "${CH_HOST}",
            "settings": {"password": "{{ env_var('CH_PASS') }}", "port": 9000},
        }
        result = interpolate_env_vars(config)
        assert result["host"] == "10.0.0.1"
        assert result["settings"]["password"] == "pw"
        assert result["settings"]["port"] == 9000

    def test_recursive_list(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://hooks/abc")
        config = [{"url": "${WEBHOOK}"}, "static"]
        result = interpolate_env_vars(config)
        assert result == [{"url": "https://hooks/abc"}, "static"]

    def test_non_string_passthrough(self):
        assert interpolate_env_vars(42) == 42
        assert interpolate_env_vars(None) is None
        assert interpolate_env_vars(True) is True

    def test_does_not_mutate_input(self, monkeypatch):
        monkeypatch.setenv("X", "y")
        original = {"k": "${X}"}
        result = interpolate_env_vars(original)
        assert original["k"] == "${X}"
        assert result["k"] == "y"
