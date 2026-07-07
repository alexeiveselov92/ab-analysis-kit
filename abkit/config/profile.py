"""
Profile configuration for abkit.

Manages database connections and locations (similar to dbt profiles).
Database manager imports are lazy so parsing configs never requires a DB
driver (``abk run --steps validate`` touches no database).
"""

from pathlib import Path

# imported lazily inside create_manager(); annotation-only here
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from abkit.utils.env_interpolation import interpolate_env_vars

if TYPE_CHECKING:  # pragma: no cover
    from abkit.database.manager import BaseDatabaseManager


class ProfileConfig(BaseModel):
    """
    Single profile configuration.

    Defines connection parameters and database locations for a specific
    environment (dev, prod, etc.).

    Attributes:
        type: Database type ("clickhouse", "postgres", "mysql")
        host: Database host
        port: Database port
        user: Database user
        password: Database password
        internal_database: Database/schema for internal tables
        internal_schema: Schema for internal tables (PostgreSQL only)
        data_database: Database for user data tables
        data_schema: Schema for user data (PostgreSQL only)
        settings: Additional database-specific settings
    """

    type: str = Field(..., description="Database type")
    host: str = Field(default="localhost", description="Database host")
    port: int = Field(..., description="Database port")
    user: str = Field(default="default", description="Database user")
    password: str = Field(default="", description="Database password")

    # Connection-target database. Required for PostgreSQL (the database to
    # connect to, inside which internal_schema/data_schema live); optional for
    # MySQL; unused for ClickHouse.
    database: str | None = Field(
        default=None, description="Database to connect to (PostgreSQL/MySQL)"
    )

    # Internal location for _ab_* tables
    internal_database: str | None = Field(
        default=None, description="Database for internal tables (ClickHouse/MySQL)"
    )
    internal_schema: str | None = Field(
        default=None, description="Schema for internal tables (PostgreSQL)"
    )

    # Data location for user tables
    data_database: str | None = Field(
        default=None, description="Database for user data tables (ClickHouse/MySQL)"
    )
    data_schema: str | None = Field(default=None, description="Schema for user data (PostgreSQL)")

    settings: dict[str, Any] = Field(
        default_factory=dict, description="Additional database settings"
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate database type."""
        allowed_types = {"clickhouse", "postgres", "mysql"}
        if v not in allowed_types:
            raise ValueError(
                f"Invalid database type: {v}. Allowed types: {', '.join(sorted(allowed_types))}"
            )
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number."""
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    def get_internal_location(self) -> str:
        """
        Get internal location (database or schema).

        Returns:
            Internal database/schema name

        Raises:
            ValueError: If location not configured
        """
        if self.type == "clickhouse":
            if not self.internal_database:
                raise ValueError("internal_database must be set for ClickHouse")
            return self.internal_database
        elif self.type == "postgres":
            if not self.internal_schema:
                raise ValueError("internal_schema must be set for PostgreSQL")
            return self.internal_schema
        elif self.type == "mysql":
            if not self.internal_database:
                raise ValueError("internal_database must be set for MySQL")
            return self.internal_database
        else:
            raise ValueError(f"Unsupported database type: {self.type}")

    def get_data_location(self) -> str:
        """
        Get data location (database or schema).

        Returns:
            Data database/schema name

        Raises:
            ValueError: If location not configured
        """
        if self.type == "clickhouse":
            if not self.data_database:
                raise ValueError("data_database must be set for ClickHouse")
            return self.data_database
        elif self.type == "postgres":
            if not self.data_schema:
                raise ValueError("data_schema must be set for PostgreSQL")
            return self.data_schema
        elif self.type == "mysql":
            if not self.data_database:
                raise ValueError("data_database must be set for MySQL")
            return self.data_database
        else:
            raise ValueError(f"Unsupported database type: {self.type}")

    def create_manager(self) -> "BaseDatabaseManager":
        """
        Create database manager from profile configuration.

        Imports the backend lazily so config parsing never requires a driver.

        Returns:
            Database manager instance

        Raises:
            ValueError: If the database type is unsupported, or required
                connection fields (e.g. PostgreSQL ``database``) are missing
            ImportError: If the backend's driver is not installed
        """
        if self.type == "clickhouse":
            from abkit.database.clickhouse_manager import ClickHouseDatabaseManager

            return ClickHouseDatabaseManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                internal_database=self.get_internal_location(),
                data_database=self.get_data_location(),
                settings=self.settings,
            )
        elif self.type == "postgres":
            if not self.database:
                raise ValueError(
                    "PostgreSQL profiles must set 'database' (the database to "
                    "connect to, inside which internal_schema/data_schema live)"
                )
            from abkit.database.postgres_manager import PostgresDatabaseManager

            return PostgresDatabaseManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                internal_schema=self.get_internal_location(),
                data_schema=self.get_data_location(),
                settings=self.settings,
            )
        elif self.type == "mysql":
            from abkit.database.mysql_manager import MySQLDatabaseManager

            return MySQLDatabaseManager(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                internal_database=self.get_internal_location(),
                data_database=self.get_data_location(),
                settings=self.settings,
            )
        else:
            raise ValueError(f"Unsupported database type: {self.type}")


class NotificationChannelConfig(BaseModel):
    """One notification channel for ``abk test-report`` (profiles.yml
    ``notification_channels:``).

    A flat block mirroring the channel constructor: a ``type`` discriminator
    (webhook / slack / mattermost / telegram / email) plus channel-specific
    params as sibling keys (``extra='allow'`` keeps them). Secrets are
    env-interpolated by :meth:`ProfilesConfig.from_yaml` before validation, so
    they are never stored in plaintext. Instantiate via
    ``abkit.notify.ChannelFactory.create_from_config(cfg.model_dump())``.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(..., description="Channel type: webhook|slack|mattermost|telegram|email")


class ProfilesConfig(BaseModel):
    """
    Container for multiple profile configurations.

    Loaded from profiles.yml file.

    Attributes:
        profiles: Dictionary mapping profile names to configurations
        default_profile: Name of default profile to use
        notification_channels: Named channels for ``abk test-report`` (optional)
    """

    profiles: dict[str, ProfileConfig]
    default_profile: str | None = None
    notification_channels: dict[str, NotificationChannelConfig] = Field(default_factory=dict)

    @field_validator("default_profile")
    @classmethod
    def validate_default_profile(cls, v: str | None, info) -> str | None:
        """Validate default profile exists."""
        if v is not None:
            profiles = info.data.get("profiles", {})
            if v not in profiles:
                raise ValueError(
                    f"default_profile '{v}' not found in profiles. "
                    f"Available profiles: {', '.join(profiles.keys())}"
                )
        return v

    @classmethod
    def from_yaml(cls, path: Path) -> "ProfilesConfig":
        """
        Load profiles from YAML file.

        Args:
            path: Path to profiles.yml

        Returns:
            ProfilesConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If YAML is invalid
        """
        if not path.exists():
            raise FileNotFoundError(f"Profiles file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Profiles file is empty")

        # Resolve ${VAR} / {{ env_var('VAR') }} placeholders before validation
        # so that secrets (DB passwords) are not stored in YAML.
        data = interpolate_env_vars(data)

        return cls.model_validate(data)

    def get_profile(self, name: str | None = None) -> ProfileConfig:
        """
        Get profile configuration by name.

        Args:
            name: Profile name (if None, use default_profile)

        Returns:
            ProfileConfig instance

        Raises:
            ValueError: If profile not found or no default set
        """
        if name is None:
            if self.default_profile is None:
                raise ValueError(
                    "No profile name specified and no default_profile set. "
                    f"Available profiles: {', '.join(self.profiles.keys())}"
                )
            name = self.default_profile

        if name not in self.profiles:
            raise ValueError(
                f"Profile '{name}' not found. "
                f"Available profiles: {', '.join(self.profiles.keys())}"
            )

        return self.profiles[name]

    def create_manager(self, profile_name: str | None = None) -> "BaseDatabaseManager":
        """
        Create database manager for a profile.

        Args:
            profile_name: Profile name (if None, use default)

        Returns:
            Database manager instance
        """
        profile = self.get_profile(profile_name)
        return profile.create_manager()
