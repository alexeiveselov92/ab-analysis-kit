"""ProfilesConfig tests: profile resolution, locations, env interpolation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import ProfileConfig, ProfilesConfig


def ch_profile(**overrides) -> dict:
    payload = {
        "type": "clickhouse",
        "host": "localhost",
        "port": 9000,
        "internal_database": "abkit_internal",
        "data_database": "analytics",
    }
    payload.update(overrides)
    return payload


class TestProfileConfig:
    def test_type_validation(self):
        with pytest.raises(ValidationError, match="Invalid database type"):
            ProfileConfig.model_validate(ch_profile(type="oracle"))

    def test_port_range(self):
        with pytest.raises(ValidationError, match="Port must be between"):
            ProfileConfig.model_validate(ch_profile(port=0))

    def test_locations_clickhouse(self):
        profile = ProfileConfig.model_validate(ch_profile())
        assert profile.get_internal_location() == "abkit_internal"
        assert profile.get_data_location() == "analytics"

    def test_locations_postgres_use_schemas(self):
        profile = ProfileConfig.model_validate(
            {
                "type": "postgres",
                "port": 5432,
                "database": "warehouse",
                "internal_schema": "abkit",
                "data_schema": "public",
            }
        )
        assert profile.get_internal_location() == "abkit"
        assert profile.get_data_location() == "public"

    def test_missing_location_raises(self):
        profile = ProfileConfig.model_validate(
            {"type": "clickhouse", "port": 9000, "data_database": "analytics"}
        )
        with pytest.raises(ValueError, match="internal_database must be set"):
            profile.get_internal_location()

    def test_postgres_requires_database_before_driver_import(self):
        """The 'database' check must fire even without psycopg2 installed."""
        profile = ProfileConfig.model_validate(
            {
                "type": "postgres",
                "port": 5432,
                "internal_schema": "abkit",
                "data_schema": "public",
            }
        )
        with pytest.raises(ValueError, match="must set 'database'"):
            profile.create_manager()


class TestProfilesConfig:
    def test_default_profile_must_exist(self):
        with pytest.raises(ValidationError, match="not found in profiles"):
            ProfilesConfig.model_validate(
                {"profiles": {"dev": ch_profile()}, "default_profile": "prod"}
            )

    def test_get_profile_by_name_and_default(self):
        config = ProfilesConfig.model_validate(
            {"profiles": {"dev": ch_profile()}, "default_profile": "dev"}
        )
        assert config.get_profile().port == 9000
        assert config.get_profile("dev").type == "clickhouse"

    def test_get_profile_missing(self):
        config = ProfilesConfig.model_validate({"profiles": {"dev": ch_profile()}})
        with pytest.raises(ValueError, match="Profile 'prod' not found"):
            config.get_profile("prod")

    def test_no_default_no_name(self):
        config = ProfilesConfig.model_validate({"profiles": {"dev": ch_profile()}})
        with pytest.raises(ValueError, match="no default_profile set"):
            config.get_profile()


class TestEnvInterpolation:
    def test_secrets_resolved_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABKIT_TEST_CH_HOST", "ch.internal")
        monkeypatch.setenv("ABKIT_TEST_CH_PASS", "s3cret")
        (tmp_path / "profiles.yml").write_text(
            """
default_profile: dev
profiles:
  dev:
    type: clickhouse
    host: ${ABKIT_TEST_CH_HOST}
    port: 9000
    password: "{{ env_var('ABKIT_TEST_CH_PASS') }}"
    internal_database: abkit_internal
    data_database: analytics
"""
        )
        config = ProfilesConfig.from_yaml(tmp_path / "profiles.yml")
        profile = config.get_profile()
        assert profile.host == "ch.internal"
        assert profile.password == "s3cret"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ProfilesConfig.from_yaml(tmp_path / "absent.yml")
