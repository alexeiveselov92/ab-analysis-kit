"""The m13 STAT-1b binding contract: ONE place decides which arm pairs exist.

``ExperimentConfig.contrast_pairs()`` is to the family what m10's
``ExperimentConfig.grid()`` is to the planner and m8's ``build_cohort_backend``
is to cohort SQL. The rule exists because the shape it prevents was already in
the tree when STAT-1b arrived: FOUR modules each carried their own
``combinations(experiment.assignment.variants, 2)`` — the analyze stage that
WRITES the rows, and the report / dashboard / notify filters that decide which
persisted rows are still declared — and ``notify/dispatch.py`` said so in a
comment ("a fourth copy should force the extraction").

Four copies of a constant are a style question. Four copies of a *knob-dependent
set* are a correctness one: a surface that resolved ``contrasts`` differently
would either chart pairs the alphas never paid for (a broken FWER claim) or drop
rows nobody warned about, and neither is visible in a diff of any one file.

The walk therefore forbids ``itertools.combinations`` outside two allowed homes:
the factory itself, and ``stats/base.py``'s generic ``compare(groups)``, which
knows nothing about experiments. It is a lint, not a sandbox — a future
legitimate use adds itself to ``ALLOWED`` with a reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
TARGET = "combinations"
FACTORY = ("ExperimentConfig", "contrast_pairs")

#: Modules allowed to enumerate pairs themselves, with the reason.
ALLOWED = {
    # the pure stats-core entry point: "all pairwise comparisons of THESE
    # groups", with no experiment and therefore no declared family in sight
    PACKAGE / "stats" / "base.py": "stats-core compare(groups) is experiment-agnostic",
    # the factory's own home
    PACKAGE / "config" / "experiment_config.py": "ExperimentConfig.contrast_pairs itself",
}


def _aliases_for_target(tree: ast.Module) -> set[str]:
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
    for i in range(len(chain) - 1):
        if (chain[i], chain[i + 1]) == FACTORY:
            return True
    return False


def test_arm_pairs_are_enumerated_only_by_the_factory():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path in ALLOWED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in _calls_to_target(tree):
            chain = _scope_chain(tree, call)
            rel = path.relative_to(PACKAGE.parent)
            where = ".".join(chain) or "<module level>"
            offenders.append(f"{rel}:{call.lineno} (in {where})")
    assert not offenders, (
        "arm pairs must come from ExperimentConfig.contrast_pairs() — a "
        "hand-rolled combinations() ignores `contrasts` and disagrees with the "
        "alphas that were divided by it:\n  " + "\n  ".join(offenders)
    )


def test_the_factory_is_where_the_allowed_call_lives():
    """The allowlist must not outlive the thing it allows: if
    ``experiment_config.py`` stops enumerating pairs, its entry is dead weight
    that would silently re-open the door for a future edit in that file."""
    tree = ast.parse((PACKAGE / "config" / "experiment_config.py").read_text())
    calls = _calls_to_target(tree)
    assert calls, "experiment_config.py no longer enumerates pairs — drop its ALLOWED entry"
    assert all(_inside_factory(_scope_chain(tree, call)) for call in calls), (
        "experiment_config.py enumerates pairs OUTSIDE contrast_pairs() — the "
        "file-level allowance was never meant to cover the whole module"
    )


def test_every_declared_family_is_reachable_from_the_factory():
    """A value of ``ContrastSet`` the factory does not branch on would silently
    fall through to ``all_pairs`` — the widest family, at the tightest alpha,
    for an experiment that asked for something else."""
    from typing import get_args

    from abkit.config.experiment_config import ContrastSet, ExperimentConfig

    payload = {
        "name": "gate",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-06",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT 1",
            "variants": ["control", "t1", "t2"],
            "expected_split": {"control": 1 / 3, "t1": 1 / 3, "t2": 1 / 3},
        },
        "comparisons": [{"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}}],
    }
    seen = {
        family: ExperimentConfig.model_validate({**payload, "contrasts": family}).contrast_pairs()
        for family in get_args(ContrastSet)
    }
    assert len(set(seen.values())) == len(seen), (
        f"two declared families produce the same pair set: {seen} — either a "
        "branch is missing or the enum value is decorative"
    )


EVASIONS = {
    "direct_import": (
        "from itertools import combinations\n"
        "def pairs(exp):\n    return list(combinations(exp.assignment.variants, 2))\n"
    ),
    "aliased_import": (
        "from itertools import combinations as combos\n"
        "def pairs(exp):\n    return list(combos(exp.assignment.variants, 2))\n"
    ),
    "module_attribute": (
        "import itertools\n"
        "def pairs(exp):\n    return list(itertools.combinations(exp.assignment.variants, 2))\n"
    ),
    "getattr_reach_around": (
        "import itertools\n"
        "def pairs(exp):\n"
        "    return list(getattr(itertools, 'combinations')(exp.assignment.variants, 2))\n"
    ),
    "module_level": (
        "from itertools import combinations\nPAIRS = list(combinations(['a', 'b'], 2))\n"
    ),
}


def test_the_gate_catches_every_evasion_shape():
    """A gate that only matches the literal name teaches people to alias."""
    for label, source in EVASIONS.items():
        tree = ast.parse(source)
        assert _calls_to_target(tree), f"{label}: the call was not even detected"


def test_the_gate_would_bite_a_hostile_module():
    """Proof the walk fires on a real file, not just on strings: drop the
    allowlist and the two legitimate homes become offenders."""
    hostile = 0
    for path in (PACKAGE / "stats" / "base.py", PACKAGE / "config" / "experiment_config.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        hostile += len(_calls_to_target(tree))
    assert hostile >= 2, "the walk no longer sees the calls the ALLOWED map excuses"
