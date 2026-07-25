"""m10 WP4: one place in ``abkit/`` may touch the global warning machinery.

``warnings.catch_warnings`` / ``simplefilter`` / ``filterwarnings`` /
``showwarning =`` all write PROCESS-global state. Since WP4, `abk explore`
answers ``/recompute`` concurrently and the driver already fans experiments out
over a thread pool, so one of those calls on a worker thread silently steals,
silences or resurrects another thread's warnings — a guard attributed to the
wrong experiment, or none at all.

`abkit/utils/warn_scope.py` is the one module allowed to do it (once,
ref-counted, with per-thread frames). Everything else uses
``capture_warnings``/``suppress_warnings``.

Why a gate and not just tests: the three converted call sites
(``tuning/recompute._compare``, ``pipeline/analyze``,
``validate/scoring.suppress_resample_warnings``) can each be reverted to the
stdlib idiom in one line, and a review round proved the behavioural tests alone
did not notice for ``pipeline/analyze`` — it has no threaded test of its own.
This walk notices, and it also covers every call site written after today.
Emitting a warning (``warnings.warn``) is untouched: this bans the SCOPES, not
the warnings.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "utils" / "warn_scope.py"
BANNED_CALLS = {"catch_warnings", "simplefilter", "filterwarnings", "resetwarnings"}
BANNED_ATTRS = {"showwarning", "_showwarnmsg_impl", "filters"}


def _global_warning_writes(tree: ast.Module) -> list[tuple[int, str]]:
    """Calls into the global warning machinery, plus writes to its globals."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None
            )
            if name in BANNED_CALLS:
                found.append((node.lineno, name))
            elif (
                isinstance(func, ast.Name)
                and func.id in {"getattr", "setattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in BANNED_CALLS | BANNED_ATTRS
            ):
                found.append((node.lineno, str(node.args[1].value)))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in BANNED_ATTRS
                    and isinstance(target.value, ast.Name)
                    and target.value.id.endswith("warnings")
                ):
                    found.append((node.lineno, f"{target.value.id}.{target.attr} ="))
    return found


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
    "filterwarnings": "import warnings\ndef f():\n    warnings.filterwarnings('ignore')\n",
    "showwarning_assignment": "import warnings\ndef f(h):\n    warnings.showwarning = h\n",
    "setattr_reach_around": "import warnings\ndef f(h):\n    setattr(warnings, 'showwarning', h)\n",
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
        "    return r, caught\n"
    )
    assert _global_warning_writes(ast.parse(allowed)) == []
