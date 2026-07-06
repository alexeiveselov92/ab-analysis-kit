"""Enforce the CLAUDE.md purity invariant for the statistical core.

``abkit.stats`` may depend on numpy/scipy/statsmodels, the stdlib and the
stdlib-only ``abkit.utils.json_utils`` canonical-hash path — never on
config/DB/Jinja/click machinery. This test keeps the invariant executable: if
``abkit/utils/__init__.py`` (or anything under ``abkit.stats``) ever grows a
heavy import, the core stops being importable standalone and this fails.
"""

from __future__ import annotations

import subprocess
import sys

# pandas is deliberately absent: statsmodels (a sanctioned dependency) imports it
# transitively; the invariant targets abkit's own config/DB/CLI machinery.
FORBIDDEN_TOP_LEVEL_MODULES = (
    "click",
    "jinja2",
    "orjson",
    "pydantic",
    "requests",
    "yaml",
)


def _check_for(module: str) -> str:
    return (
        f"import sys; import {module}; "
        f"forbidden = set({FORBIDDEN_TOP_LEVEL_MODULES!r}); "
        "loaded = {name.split('.')[0] for name in sys.modules}; "
        "bad = sorted(forbidden & loaded); "
        f"assert not bad, f'{module} pulled in forbidden imports: {{bad}}'"
    )


def test_stats_core_imports_no_forbidden_dependencies() -> None:
    subprocess.run([sys.executable, "-c", _check_for("abkit.stats")], check=True)


def test_sequential_module_imports_no_forbidden_dependencies() -> None:
    # The M5 sequential engine takes plain primitives — no config/pydantic type may
    # cross into abkit.stats (docs/specs/m5-implementation-plan.md D5).
    subprocess.run([sys.executable, "-c", _check_for("abkit.stats.sequential")], check=True)
