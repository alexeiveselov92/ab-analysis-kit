"""m10 WP4: one place in ``abkit/`` may touch the global warning machinery.

``warnings.catch_warnings`` / ``simplefilter`` / ``filterwarnings`` /
``showwarning =`` / a poke at ``warnings.filters`` all write PROCESS-global
state. Since WP4, `abk explore` answers ``/recompute`` concurrently and the
driver already fans experiments out over a thread pool, so one of those calls
on a worker thread silently steals, silences or resurrects another thread's
warnings — a guard attributed to the wrong experiment, or none at all.

`abkit/utils/warn_scope.py` is the one module allowed to do it (once,
ref-counted, with per-thread frames). Everything else uses
``capture_warnings``/``suppress_warnings``.

Why a gate and not just tests: the three converted call sites
(``tuning/recompute._compare``, ``pipeline/analyze``,
``validate/scoring.suppress_resample_warnings``) can each be reverted to the
stdlib idiom in one line, and a review round proved the behavioural tests alone
did not notice for ``pipeline/analyze`` — it has no threaded test of its own.
This walk notices, and it also covers every call site written after today.

The walk RESOLVES IMPORT ALIASES rather than matching identifiers (review
round 2 found seven working spellings escaping the first version: ``import
warnings as w``, ``from warnings import catch_warnings as cw``, ``filters[:] =
[]``, ``filters.insert(...)``, ``filters += [...]``, a bare rebinding of a
banned member, and an annotated assignment). Emitting a warning
(``warnings.warn``) is untouched: this bans the SCOPES, not the warnings.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "utils" / "warn_scope.py"
#: members of ``warnings`` that write (or hand out the ability to write) the
#: process-global filter list / recorder
BANNED = {
    "catch_warnings",
    "simplefilter",
    "filterwarnings",
    "resetwarnings",
    "showwarning",
    "filters",
    "_showwarnmsg_impl",
    "_filters_mutated",
}


def _module_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """``(names bound to the warnings module, names bound to a banned member)``."""
    modules: set[str] = set()
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "warnings" or alias.name.endswith(".warnings"):
                    modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module == "warnings":
            for alias in node.names:
                if alias.name in BANNED:
                    members.add(alias.asname or alias.name)
    return modules, members


def _is_banned_ref(node: ast.AST, modules: set[str], members: set[str]) -> str | None:
    """``warnings.<banned>`` (through any alias) or a locally-bound member."""
    if isinstance(node, ast.Attribute) and node.attr in BANNED:
        base = node.value
        if isinstance(base, ast.Name) and (base.id in modules or base.id.endswith("warnings")):
            return f"{base.id}.{node.attr}"
        if isinstance(base, ast.Subscript):  # sys.modules['warnings'].showwarning
            return f"<subscript>.{node.attr}"
    if isinstance(node, ast.Name) and node.id in members:
        return node.id
    return None


def _global_warning_writes(tree: ast.Module) -> list[tuple[int, str]]:
    """Every reference in this module that can write the global warning state."""
    modules, members = _module_aliases(tree)
    found: list[tuple[int, str]] = []

    def flag(node: ast.AST, what: str | None) -> None:
        if what is not None:
            found.append((getattr(node, "lineno", 0), what))

    for node in ast.walk(tree):
        # a call, an alias-rebinding, or a bare read that hands the hook out
        if isinstance(node, ast.Attribute | ast.Name):
            flag(node, _is_banned_ref(node, modules, members))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in BANNED
        ):
            flag(node, f"{node.func.id}(…, {node.args[1].value!r})")
    # dedupe: one report per (line, name)
    return sorted(set(found))


def test_only_warn_scope_touches_the_global_warning_machinery():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DEFINITION:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, what in _global_warning_writes(tree):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno} ({what})")
    assert not offenders, (
        "the warning filter list and showwarning are PROCESS-global — on a "
        "worker thread they steal or silence another thread's warnings. Use "
        "abkit.utils.warn_scope's capture_warnings/suppress_warnings:\n  " + "\n  ".join(offenders)
    )


EVASIONS = {
    "the_recompute_revert": (
        "import warnings as _warnings\n"
        "def compare(m, a, b):\n"
        "    with _warnings.catch_warnings(record=True) as caught:\n"
        "        _warnings.simplefilter('always', W)\n"
        "        return m.compare_pair(a, b), caught\n"
    ),
    "the_scoring_revert": (
        "import warnings\n"
        "def wrap(fn):\n"
        "    with warnings.catch_warnings():\n"
        "        warnings.simplefilter('ignore', W)\n"
        "        return fn()\n"
    ),
    "bare_import_form": (
        "from warnings import catch_warnings\n"
        "def f():\n    with catch_warnings():\n        pass\n"
    ),
    "aliased_import_form": (
        "from warnings import catch_warnings as cw, simplefilter as sf\n"
        "def f():\n    with cw(record=True):\n        sf('always')\n"
    ),
    "aliased_module_assignment": "import warnings as w\ndef f(h):\n    w.showwarning = h\n",
    "rebound_member": "import warnings as w\n_cw = w.catch_warnings\n",
    "filters_slice_assignment": "import warnings\ndef f():\n    warnings.filters[:] = []\n",
    "filters_insert": "import warnings\ndef f(e):\n    warnings.filters.insert(0, e)\n",
    "filters_augmented": "import warnings\ndef f(e):\n    warnings.filters += [e]\n",
    "annotated_assignment": (
        "import warnings\nfrom typing import Any\ndef f(h):\n" "    warnings.showwarning: Any = h\n"
    ),
    "sys_modules_reach_around": (
        "import sys\ndef f(h):\n    sys.modules['warnings'].showwarning = h\n"
    ),
    "filterwarnings": "import warnings\ndef f():\n    warnings.filterwarnings('ignore')\n",
    "setattr_reach_around": "import warnings\ndef f(h):\n    setattr(warnings, 'showwarning', h)\n",
    "private_impl_hook": ("import warnings\ndef f(h):\n    warnings._showwarnmsg_impl = h\n"),
}


def test_the_gate_catches_every_revert_shape():
    for label, source in EVASIONS.items():
        assert _global_warning_writes(ast.parse(source)), label


def test_the_gate_leaves_ordinary_warning_use_alone():
    """Raising a warning, and the sanctioned scopes, must not trip it."""
    allowed = (
        "import warnings\n"
        "from abkit.utils.warn_scope import capture_warnings, suppress_warnings\n"
        "def f(m, a, b):\n"
        "    warnings.warn('a guard', UserWarning, stacklevel=2)\n"
        "    with capture_warnings(UserWarning) as caught:\n"
        "        r = m.compare_pair(a, b)\n"
        "    with suppress_warnings(UserWarning):\n"
        "        m.compare_pair(a, b)\n"
        "    result_warnings = list(caught)\n"  # a local NAMED like the module
        "    return r, result_warnings\n"
    )
    assert _global_warning_writes(ast.parse(allowed)) == []
