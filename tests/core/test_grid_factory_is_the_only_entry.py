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
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "core" / "period_planner.py"
FACTORY = ("ExperimentConfig", "grid")


def _calls_to(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def _enclosing_scope(tree: ast.Module, call: ast.Call) -> tuple[str | None, str | None]:
    """(class name, function name) containing *call*, innermost-first."""
    class_name = function_name = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if child is call:
                    if isinstance(node, ast.ClassDef):
                        class_name = node.name
                    else:
                        function_name = node.name
                    break
    return class_name, function_name


def test_generate_grid_is_called_only_through_the_factory():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DEFINITION:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in _calls_to(tree, "generate_grid"):
            scope = _enclosing_scope(tree, call)
            if scope != FACTORY:
                rel = path.relative_to(PACKAGE.parent)
                offenders.append(f"{rel}:{call.lineno} (in {scope[0]}.{scope[1]})")
    assert not offenders, (
        "generate_grid must only be called from ExperimentConfig.grid() — a "
        "hand-copied argument list silently drops planner knobs:\n  "
        + "\n  ".join(offenders)
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
