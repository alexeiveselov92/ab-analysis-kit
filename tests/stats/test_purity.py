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

_CHECK = (
    "import sys; import abkit.stats; "
    f"forbidden = set({FORBIDDEN_TOP_LEVEL_MODULES!r}); "
    "loaded = {name.split('.')[0] for name in sys.modules}; "
    "bad = sorted(forbidden & loaded); "
    "assert not bad, f'abkit.stats pulled in forbidden imports: {bad}'"
)


def test_stats_core_imports_no_forbidden_dependencies() -> None:
    subprocess.run([sys.executable, "-c", _CHECK], check=True)
