"""The m10 WP1 binding contract: ONE place composes an experiment's grid.

``ExperimentConfig.grid()`` is to the planner what m8's ``build_cohort_backend``
is to cohort SQL — the single seam where window + cadence + anchor come
together. The rule exists because the failure it prevents already happened
once during this milestone: ``interval_anchor`` was added to
``generate_grid``'s signature and to the config, and every one of the eight
production call sites kept passing its hand-copied argument list, so the knob
silently did nothing. A reviewer cannot see that; an AST walk can.

``generate_grid`` itself keeps its primitive signature — tests build grids
directly, and ``abkit.core`` stays free of config imports.

The walk resolves import aliases and flags ``getattr`` reach-arounds, because
a gate that only matches the literal name teaches contributors to rename their
way past it. It is a lint, not a sandbox: it catches the mistake, not an
adversary.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "core" / "period_planner.py"
TARGET = "generate_grid"
FACTORY = ("ExperimentConfig", "grid")


def _aliases_for_target(tree: ast.Module) -> set[str]:
    """Every local name bound to ``generate_grid``, plus module aliases.

    Covers ``from … import generate_grid``, ``… as _gg``, and
    ``import abkit.core.period_planner as pp`` (whose calls arrive as
    ``pp.generate_grid``, matched by attribute name).
    """
    names = {TARGET}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == TARGET and alias.asname:
                    names.add(alias.asname)
    return names


def _calls_to_target(tree: ast.Module) -> list[ast.Call]:
    names = _aliases_for_target(tree)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in names:
            calls.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == TARGET:
            calls.append(node)
        elif (
            # getattr(module, "generate_grid")(...) — the reach-around
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and len(func.args) >= 2
            and isinstance(func.args[1], ast.Constant)
            and func.args[1].value == TARGET
        ):
            calls.append(node)
    return calls


def _scope_chain(tree: ast.Module, call: ast.Call) -> list[str]:
    """Class/function names enclosing *call*, outermost first."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    chain: list[str] = []
    node: ast.AST | None = call
    while node is not None:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            chain.append(node.name)
        node = parents.get(node)
    return list(reversed(chain))


def _inside_factory(chain: list[str]) -> bool:
    """True when the call sits in ExperimentConfig.grid — nesting included.

    A helper ``def`` inside the factory is still the factory; only a call that
    escapes it counts.
    """
    for i in range(len(chain) - 1):
        if (chain[i], chain[i + 1]) == FACTORY:
            return True
    return False


def test_generate_grid_is_called_only_through_the_factory():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DEFINITION:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in _calls_to_target(tree):
            chain = _scope_chain(tree, call)
            if not _inside_factory(chain):
                rel = path.relative_to(PACKAGE.parent)
                where = ".".join(chain) or "<module level>"
                offenders.append(f"{rel}:{call.lineno} (in {where})")
    assert not offenders, (
        "generate_grid must only be called from ExperimentConfig.grid() — a "
        "hand-copied argument list silently drops planner knobs:\n  " + "\n  ".join(offenders)
    )


def test_the_factory_forwards_every_grid_shaping_field():
    """A field the factory forgets is a knob that exists in YAML and does
    nothing — the exact bug this file was written for."""
    source = (PACKAGE / "config" / "experiment_config.py").read_text()
    tree = ast.parse(source)
    factory = next(
        node
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and cls.name == "ExperimentConfig"
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "grid"
    )
    forwarded = {
        node.attr
        for node in ast.walk(factory)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    for field in ("start_ts", "horizon_ts", "timezone", "interval_anchor"):
        assert field in forwarded, f"ExperimentConfig.grid() never reads {field}"


EVASIONS = {
    "aliased_import": (
        "from abkit.core.period_planner import generate_grid as _gg\n"
        "def build():\n    return _gg(1, 2, [])\n"
    ),
    "module_attribute": (
        "from abkit.core import period_planner as pp\n"
        "def build():\n    return pp.generate_grid(1, 2, [])\n"
    ),
    "getattr_reach_around": (
        "from abkit.core import period_planner as pp\n"
        "def build():\n    return getattr(pp, 'generate_grid')(1, 2, [])\n"
    ),
    "module_level": "from abkit.core.period_planner import generate_grid\nGRID = generate_grid(1, 2, [])\n",
    "wrong_class": (
        "from abkit.core.period_planner import generate_grid\n"
        "class NotTheConfig:\n    def grid(self):\n        return generate_grid(1, 2, [])\n"
    ),
}

ALLOWED = {
    "the_factory_itself": (
        "class ExperimentConfig:\n"
        "    def grid(self, *, limit=None):\n"
        "        from abkit.core.period_planner import generate_grid\n"
        "        return generate_grid(self.start_ts, self.horizon_ts, [], limit=limit)\n"
    ),
    "nested_helper_inside_the_factory": (
        "class ExperimentConfig:\n"
        "    def grid(self, *, limit=None):\n"
        "        from abkit.core.period_planner import generate_grid\n"
        "        def _build():\n            return generate_grid(self.start_ts, self.horizon_ts, [])\n"
        "        return _build()\n"
    ),
}


def test_the_gate_catches_every_evasion_shape():
    """A gate that only matches the literal name teaches people to alias."""
    for label, source in EVASIONS.items():
        tree = ast.parse(source)
        calls = _calls_to_target(tree)
        assert calls, f"{label}: the call was not even detected"
        assert not any(_inside_factory(_scope_chain(tree, c)) for c in calls), label


def test_the_gate_does_not_flag_the_factory_or_its_helpers():
    for label, source in ALLOWED.items():
        tree = ast.parse(source)
        calls = _calls_to_target(tree)
        assert calls, f"{label}: fixture must contain a call"
        assert all(_inside_factory(_scope_chain(tree, c)) for c in calls), label
