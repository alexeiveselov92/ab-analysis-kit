"""STAT-3a's AST gate: no inversion without the guard, no guard without the method.

The refusal is worth exactly as much as the set of places it covers. Two ways to lose
it, neither visible in a diff review:

1. calling a guarded entry without the ``method`` it must judge — impossible at runtime
   (the argument is keyword-only and required), but a call that is never executed by a
   test would only be discovered by an operator;
2. open-coding the arithmetic — ``se = ci_length / (2z)``, or its ``(right − left)``
   twin — instead of calling the guarded helper. This is not hypothetical: it is
   exactly what ``tuning/recompute._alpha_inverted_bounds`` (the explore α tier) did,
   which is why the design's count of eleven entry points was eleven and not twelve.

Both rules are DERIVED from the source, never a hand-maintained list, and both are
proven to bite on a hostile fixture below.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[3] / "abkit"

#: Entries whose ``method`` argument IS the guard's input.
GUARDED_ENTRIES = frozenset(
    {
        "se_from_ci_length",
        "se_from_ci_length_array",
        "to_always_valid",
        "_alpha_inverted_bounds",
    }
)

GUARD = "require_symmetric_ci"

#: Names whose value is a CI WIDTH (numerator of an inversion).
_WIDTH_SUFFIXES = ("ci_length", "ci_width")
#: Name pairs whose difference is a CI width.
_UPPER = ("right", "right_bound", "hi", "upper")
_LOWER = ("left", "left_bound", "lo", "lower")


def _python_files() -> list[pathlib.Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _leaf_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_ci_width(node: ast.AST) -> bool:
    """Does this expression evaluate to the width of a confidence interval?"""
    name = _leaf_name(node)
    if name is not None:
        return name.endswith(_WIDTH_SUFFIXES)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
        hi, lo = _leaf_name(node.left), _leaf_name(node.right)
        return hi in _UPPER and lo in _LOWER
    return False


def _mentions_a_quantile(node: ast.AST) -> bool:
    """Does the denominator involve a normal quantile — i.e. is this an SE recovery?

    Dividing a CI width by a QUANTILE recovers a standard error and assumes symmetry;
    dividing it by 2 is a half-width, which is a display summary and assumes nothing
    (``readout``'s FLAT power check and explore's ``ci_half`` chip both do the latter —
    deliberately not inversions). A hardcoded quantile (``/ 3.92``) is caught by the
    constant rule, so the shortcut cannot be used to slip past the name test.
    """
    for child in ast.walk(node):
        name = _leaf_name(child)
        if name is not None and ("z" in name.lower() or name in {"ppf", "ndtri", "isf"}):
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, int | float):
            if float(child.value) not in (1.0, 2.0):
                return True
    return False


def _inversion_sites(tree: ast.AST) -> list[ast.BinOp]:
    """Every ``<ci width> / <quantile…>`` — the SE-by-CI-inversion arithmetic."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and _is_ci_width(node.left)
        and _mentions_a_quantile(node.right)
    ]


def _enclosing_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _guarded_function_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Line spans of functions whose own body calls the guard."""
    spans = []
    for fn in _enclosing_functions(tree):
        if any(_called_name(call) == GUARD for call in ast.walk(fn) if isinstance(call, ast.Call)):
            spans.append((fn.lineno, fn.end_lineno or fn.lineno))
    return spans


# --- rule 1: every guarded entry is called WITH a method ------------------------------


def test_every_call_to_a_guarded_entry_names_its_method() -> None:
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            if _called_name(call) not in GUARDED_ENTRIES:
                continue
            if not any(kw.arg == "method" for kw in call.keywords):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{call.lineno}")

    assert offenders == [], (
        "these calls invert a CI without declaring whose it is: "
        + ", ".join(offenders)
        + " — pass method=<the BOUND instance that built the interval>"
    )


def test_the_entry_roster_is_reachable() -> None:
    """Sanity: the names above still exist as call sites, so rule 1 can fail."""
    seen = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            name = _called_name(call)
            if name in GUARDED_ENTRIES:
                seen.add(name)
    assert seen == set(
        GUARDED_ENTRIES
    ), f"unreachable entries in the roster: {GUARDED_ENTRIES - seen}"


# --- rule 2: nobody open-codes the inversion outside a guarded function ---------------


def test_every_open_coded_inversion_sits_inside_a_guarded_function() -> None:
    offenders = []
    for path in _python_files():
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        spans = _guarded_function_spans(tree)
        for site in _inversion_sites(tree):
            if not any(start <= site.lineno <= end for start, end in spans):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{site.lineno}")

    assert offenders == [], (
        "these divide a CI width without calling "
        + GUARD
        + ": "
        + ", ".join(offenders)
        + " — recovering an SE from an interval assumes it is effect ± z·SE; call "
        "sequential.se_from_ci_length, or guard the function explicitly"
    )


def test_the_known_inversions_are_found_and_guarded() -> None:
    """The rule must MATCH something, or it is a green light over an empty set."""
    found = {
        str(path.relative_to(PACKAGE.parent))
        for path in _python_files()
        if _inversion_sites(ast.parse(path.read_text(), filename=str(path)))
    }
    assert found == {
        "abkit/stats/sequential/confidence_sequence.py",
        "abkit/tuning/recompute.py",
    }, f"the set of CI inversions moved: {sorted(found)}"


# --- both rules are proven to bite ----------------------------------------------------


HOSTILE_UNGUARDED_CALL = """
from abkit.stats.sequential import se_from_ci_length
se = se_from_ci_length(result.ci_length, alpha)
"""

HOSTILE_OPEN_CODED = """
def recover(result, z):
    return result.ci_length / (2.0 * z)
"""

HOSTILE_OPEN_CODED_SUBTRACTION = """
def recover(left, right, z):
    return (right - left) / (2.0 * z)
"""

#: The shortcut that would dodge a name-only rule: 2·z at α=0.05, written out.
HOSTILE_HARDCODED_QUANTILE = """
def recover(result):
    return result.ci_length / 3.9199
"""

#: NOT an inversion: a half-width recovers nothing and assumes nothing.
INNOCENT_HALF_WIDTH = """
def chip(left, right):
    return (right - left) / 2.0
"""


def test_rule_1_bites() -> None:
    tree = ast.parse(HOSTILE_UNGUARDED_CALL)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    bad = [c for c in calls if _called_name(c) in GUARDED_ENTRIES and not c.keywords]
    assert len(bad) == 1


@pytest.mark.parametrize(
    "source", [HOSTILE_OPEN_CODED, HOSTILE_OPEN_CODED_SUBTRACTION, HOSTILE_HARDCODED_QUANTILE]
)
def test_rule_2_bites(source: str) -> None:
    tree = ast.parse(source)
    assert _inversion_sites(tree), "the hostile inversion was not detected"
    assert _guarded_function_spans(tree) == [], "the hostile function was read as guarded"


def test_rule_2_leaves_a_half_width_alone() -> None:
    """The rule must not be so broad that guarding everything becomes the only way out."""
    assert _inversion_sites(ast.parse(INNOCENT_HALF_WIDTH)) == []
