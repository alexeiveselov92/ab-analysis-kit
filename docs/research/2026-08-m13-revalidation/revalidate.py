"""M13 STAT-6 — the batch A/A revalidation over every knob the milestone added.

Run from the repo root:  .venv/bin/python <this file>
"""

from __future__ import annotations

import json
import sys

sys.path[:0] = ["tests/_helpers", "tests/validate", "."]

from abkit.config.method_config import MethodConfig  # noqa: E402
from abkit.validate.family import FamilyMember, sweep_family  # noqa: E402
from abkit.validate.scoring import score_cell  # noqa: E402
from tests.validate._panels import fraction_panel, normal_panel  # noqa: E402

ALPHA = 0.05
ITERATIONS = 20_000
FAMILY_ITERATIONS = 20_000

rows = []


def bind(name, **params):
    return MethodConfig(name=name, params=params).bind(alpha=ALPHA)


def record(case, knob, value, score, extra=None):
    rows.append(
        {
            "case": case,
            "knob": knob,
            "value": value,
            "iterations": score.valid_iterations,
            "fpr": score.fpr,
            "peeking_fpr": score.peeking_fpr,
            "fpr_negative_share": score.fpr_negative_share,
            "power": score.power,
            "coverage": score.coverage,
            "effect_exaggeration": score.effect_exaggeration,
            **(extra or {}),
        }
    )
    print(json.dumps(rows[-1]), flush=True)


# ── 1. STAT-3: the proportion interval ─────────────────────────────────────────
prop = fraction_panel(n_units=4000, seed=90210, base_rate=0.08)
for interval in ("pooled", "score"):
    for scale in ("absolute", "relative"):
        score = score_cell(
            prop,
            bind("z-test", test_type=scale, interval=interval),
            iterations=ITERATIONS,
            # PAIRED: the same placebo draws for both intervals, so a
            # difference between the two rows cannot be Monte-Carlo noise.
            seed_parts=("m13", "prop", scale),
            inject_effect=0.25 if scale == "relative" else 0.02,
        )
        record(f"z-test/{scale}", "interval", interval, score)

# ── 2. STAT-4: the relative mean interval, in the regime the defect lives in ───
# σ/μ = 1 at 400 units per arm ⇒ the control mean's CV ≈ 5%.
for label, mu, sigma, n_units in (("cv5", 50.0, 50.0, 800), ("cv10", 50.0, 100.0, 800)):
    panel = normal_panel(n_units=n_units, n_cutoffs=1, seed=4242, mu=mu, sigma=sigma)
    for interval in ("delta", "fieller"):
        score = score_cell(
            panel,
            bind("t-test", test_type="relative", interval=interval),
            iterations=ITERATIONS,
            seed_parts=("m13", label),  # paired, as above
            inject_effect=0.15,
        )
        record(f"t-test/relative/{label}", "interval", interval, score)

# ── 3. STAT-1c: an uncorrected guardrail ──────────────────────────────────────
# The knob resolves an ALPHA, so what A/A can check is that the level it hands
# the guardrail is honoured by the estimator: raw α vs the secondary tier's α/k.
guard_panel = normal_panel(n_units=3000, n_cutoffs=1, seed=777, mu=20.0, sigma=6.0)
for label, alpha in (("inherit (α/3)", ALPHA / 3), ("none (raw α)", ALPHA)):
    method = MethodConfig(name="t-test", params={"test_type": "absolute"}).bind(alpha=alpha)
    score = score_cell(
        guard_panel, method, iterations=ITERATIONS, seed_parts=("m13", "guardrail", label)
    )
    record("t-test/guardrail", "guardrail_correction", label, score, {"alpha": alpha})

# ── 4. STAT-1: the correction layer, over the composed family ─────────────────
members_raw = [
    FamilyMember(
        metric=f"m{i}",
        panel=normal_panel(n_units=2000, n_cutoffs=1, seed=500 + i, mu=50.0, sigma=10.0),
        method=bind("t-test", test_type="absolute"),
        alpha=ALPHA,
        planted=False,
    )
    for i in range(4)
]
members_bonf = [
    FamilyMember(
        metric=m.metric,
        panel=m.panel,
        method=MethodConfig(name="t-test", params={"test_type": "absolute"}).bind(alpha=ALPHA / 4),
        alpha=ALPHA / 4,
        planted=False,
    )
    for m in members_raw
]
for label, members, scheme in (
    ("bonferroni α/4", members_bonf, "bonferroni"),
    ("holm (raw α)", members_raw, "holm"),
    ("none (raw α)", members_raw, "none"),
):
    sweep = sweep_family(
        members, correction=scheme, iterations=FAMILY_ITERATIONS, share_a=0.5, seed_parts=("m13",)
    )
    rows.append(
        {
            "case": "family of 4 null metrics",
            "knob": "correction",
            "value": label,
            "iterations": sweep.valid_iterations,
            "fwer": sweep.fwer,
            "fdr": sweep.fdr,
            "any_rejection_rate": sweep.any_rejection_rate,
        }
    )
    print(json.dumps(rows[-1]), flush=True)


# ── 5. …and the same family with two REAL effects planted ────────────────────
# Under the complete null Holm and one-step Bonferroni are the same event
# (`min p ≤ α/m`), so section 4 cannot separate them by construction. It can
# only be measured where some hypotheses are false.
def planted(members, count):
    return [
        FamilyMember(
            metric=m.metric, panel=m.panel, method=m.method, alpha=m.alpha, planted=i < count
        )
        for i, m in enumerate(members)
    ]


for label, members, scheme in (
    ("bonferroni α/4", members_bonf, "bonferroni"),
    ("holm (raw α)", members_raw, "holm"),
):
    sweep = sweep_family(
        planted(members, 2),
        correction=scheme,
        iterations=FAMILY_ITERATIONS,
        share_a=0.5,
        seed_parts=("m13", "planted"),
        inject_effect=1.2,
    )
    rows.append(
        {
            "case": "family of 4, two planted",
            "knob": "correction",
            "value": label,
            "iterations": sweep.valid_iterations,
            "fwer": sweep.fwer,
            "fdr": sweep.fdr,
            "any_rejection_rate": sweep.any_rejection_rate,
        }
    )
    print(json.dumps(rows[-1]), flush=True)

with open(sys.argv[1] if len(sys.argv) > 1 else "revalidation.json", "w") as handle:
    json.dump(rows, handle, indent=1)
print(f"\n{len(rows)} rows", file=sys.stderr)
