"""Environment variable interpolation for configuration values.

Supports two syntaxes inside string values:

- ``${VAR_NAME}`` — shell-style.
- ``{{ env_var('VAR_NAME') }}`` — dbt-style.

Unresolved placeholders (variable not set) are kept as-is so that callers
get a chance to validate or report missing environment variables instead
of silently falling back to an empty string.
"""

from __future__ import annotations

import os
import re
from typing import Any

_SHELL_PATTERN = re.compile(r"\$\{([^}]+)\}")
_DBT_PATTERN = re.compile(r"\{\{\s*env_var\(['\"]([^'\"]+)['\"]\)\s*\}\}")


def interpolate_env_vars(value: Any) -> Any:
    """Recursively interpolate environment variables in *value*.

    Strings are scanned for both supported placeholder syntaxes; mappings
    and sequences are walked depth-first. Other types pass through
    unchanged.
    """
    if isinstance(value, str):
        return _interpolate_string(value)
    if isinstance(value, dict):
        return {k: interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env_vars(item) for item in value]
    if isinstance(value, tuple):
        return tuple(interpolate_env_vars(item) for item in value)
    return value


def _interpolate_string(value: str) -> str:
    value = _SHELL_PATTERN.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )
    value = _DBT_PATTERN.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )
    return value
