"""The m14 DEC-1 binding contract: ONE place resolves which arm is the control.

``ExperimentConfig.control`` is to the baseline what m10's ``grid()`` is to the
planner, m13's ``contrast_pairs()`` is to the family, and m8's
``build_cohort_backend`` is to cohort SQL. The rule exists because the shape it
prevents was already in the tree when DEC-1 arrived: **seven** sites spelled
``variants[0]`` to mean "the control", and they do not fail the same way.

* ``contrast_pairs()`` would build the wrong family — silently the wrong alphas.
* ``readout.evaluate`` would verdict against the wrong baseline.
* ``readout._srm_from_series`` would fail **SILENTLY**: every
  ``(metric, control, treatment)`` series lookup misses, and a miss is
  indistinguishable there from "no rows yet", so the experiment reads
  ``srm_flag=False, srm_pvalue=None`` — a broken assignment looks healthy.
* ``plan.py`` would size against the wrong arm; ``validate/runner._share_a``
  would calibrate at the wrong split ratio; ``test_report.py`` is cosmetic.

A knob that reaches none of its call sites is the m10 ``interval_anchor``
failure. A knob whose missed call site turns a **safety gate quiet** is worse,
and that is the whole reason DEC-1 is its own work package rather than a line in
DEC-2.

**What the walk models.** Any ``…variants[0]`` (the control) and any
``…variants[1:]`` (the treatments) under ``abkit/``, whether the subscripted
expression is an attribute chain (``experiment.assignment.variants``) or a local
alias (``variants = experiment.assignment.variants`` — the shape plan.py used).

**What it does NOT model**, stated so nobody reads more into it: an
``ExperimentConfig`` is not the only thing with a ``variants`` attribute, and a
baseline can be resolved without a subscript at all (``next(iter(...))``, an
unpacking, ``min()`` over the list). The walk is a lint against the shape that
was actually in the tree, not a sandbox. A future legitimate use adds itself to
``ALLOWED`` with a reason — and the audit that opened DEC-1 found no site that
means "the first declared arm" for a NON-control reason, so the allowlist has
exactly one entry: the resolver.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
ATTRIBUTE = "variants"
#: The two ``ExperimentConfig`` members allowed to read the convention: the
#: resolver itself, and the predicate that answers "did a declaration move the
#: baseline off it?" — which cannot be written without the same subscript.
RESOLVER_CLASS = "ExperimentConfig"
RESOLVER_MEMBERS = {"control", "control_reorients_pairs"}

#: Modules allowed to resolve the baseline positionally, with the reason.
ALLOWED = {
    # the resolver's own home — scope-checked below, so the file-level
    # allowance cannot quietly cover the rest of the module
    PACKAGE
    / "config"
    / "experiment_config.py": "ExperimentConfig.control itself",
}


def _is_variants(node: ast.expr) -> bool:
    """``…​.variants`` or a bare local named ``variants``.

    The local form is not paranoia: ``plan.py`` bound
    ``variants = experiment.assignment.variants`` and then subscripted the
    alias four times, so an attribute-only walk would have seen none of them.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == ATTRIBUTE
    return isinstance(node, ast.Name) and node.id == ATTRIBUTE


def _positional_control_reads(tree: ast.Module) -> list[tuple[int, str]]:
    """``variants[0]`` (the control) and ``variants[1:]`` (the treatments)."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript) or not _is_variants(node.value):
            continue
        index = node.slice
        if isinstance(index, ast.Constant) and index.value == 0:
            found.append((node.lineno, "variants[0]"))
        elif (
            isinstance(index, ast.Slice)
            and index.upper is None
            and index.step is None
            and isinstance(index.lower, ast.Constant)
            and index.lower.value == 1
        ):
            found.append((node.lineno, "variants[1:]"))
    return found


def _scope_chain(tree: ast.Module, target: ast.AST) -> list[str]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    chain: list[str] = []
    node: ast.AST | None = target
    while node is not None:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            chain.append(node.name)
        node = parents.get(node)
    return list(reversed(chain))


def _inside_resolver(chain: list[str]) -> bool:
    return any(
        chain[i] == RESOLVER_CLASS and chain[i + 1] in RESOLVER_MEMBERS
        for i in range(len(chain) - 1)
    )


def test_the_baseline_is_resolved_only_by_the_property():
    offenders: list[str] = []
    scanned = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        if path in ALLOWED:
            continue
        scanned += 1
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, shape in _positional_control_reads(tree):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno} ({shape})")
    # a gate that scans nothing passes forever: a `src/` move or a renamed
    # package would empty the rglob and leave `offenders` trivially empty
    assert scanned > 50, f"the walk only reached {scanned} modules — is PACKAGE right?"
    assert not offenders, (
        "the control arm must come from ExperimentConfig.control and the "
        "treatments from ExperimentConfig.treatments — a positional read "
        "ignores `assignment.control`, and in _srm_from_series it does so "
        "SILENTLY (every series lookup misses ⇒ srm_flag=False):\n  " + "\n  ".join(offenders)
    )


def test_the_resolver_is_where_the_allowed_read_lives():
    """The allowlist must not outlive the thing it allows.

    ``experiment_config.py`` is excused as a FILE, but only the property may
    use the excuse: if ``contrast_pairs()`` or ``catalog_record()`` grew a
    positional read of its own, the module-level entry would silently cover it.
    """
    tree = ast.parse((PACKAGE / "config" / "experiment_config.py").read_text())
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and _is_variants(node.value)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
    ]
    assert reads, "experiment_config.py no longer resolves the baseline — drop its ALLOWED entry"
    assert all(_inside_resolver(_scope_chain(tree, read)) for read in reads), (
        "experiment_config.py resolves the baseline OUTSIDE ExperimentConfig."
        "control — the file-level allowance was never meant to cover the module"
    )


EVASIONS = {
    "attribute_chain": ("def control(exp):\n    return exp.assignment.variants[0]\n"),
    "local_alias": (
        "def control(exp):\n" "    variants = exp.assignment.variants\n" "    return variants[0]\n"
    ),
    "treatment_slice": ("def treatments(exp):\n    return exp.assignment.variants[1:]\n"),
    "module_level": ("variants = ['a', 'b']\nCONTROL = variants[0]\n"),
}

#: Shapes that must NOT be flagged — a gate that fires on these is a gate
#: people learn to work around.
INNOCENT = {
    # the treatments, but sliced from the wrong end — not the control shape
    "trailing_slice": "def x(exp):\n    return exp.assignment.variants[:1]\n",
    # a different list entirely
    "other_list": "def x(exp):\n    return exp.comparisons[0]\n",
    # indexing a treatment, which is DEC-1-neutral (it is not the baseline)
    "second_arm": "def x(exp):\n    return exp.assignment.variants[1]\n",
}


def test_the_gate_catches_every_evasion_shape():
    for label, source in EVASIONS.items():
        assert _positional_control_reads(
            ast.parse(source)
        ), f"{label}: the positional read was not even detected"


def test_the_gate_leaves_innocent_shapes_alone():
    for label, source in INNOCENT.items():
        assert not _positional_control_reads(
            ast.parse(source)
        ), f"{label}: flagged a shape that does not resolve the baseline"


def test_the_gate_would_bite_a_hostile_module():
    """Proof the walk fires on a real file, not just on strings: drop the
    allowlist and the resolver's own home becomes an offender."""
    tree = ast.parse((PACKAGE / "config" / "experiment_config.py").read_text())
    assert _positional_control_reads(
        tree
    ), "the walk no longer sees the read the ALLOWED map excuses"


def test_the_module_level_evasion_is_reported_with_its_line():
    """The failure message has to be actionable — a bare boolean would send the
    next author grepping a 900-line module."""
    reads = _positional_control_reads(ast.parse("V = ['a']\nC = V[0]\n".replace("V", "variants")))
    assert reads == [(2, "variants[0]")]
