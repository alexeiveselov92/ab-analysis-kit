# M13 blind re-derivations + code audit (2026-08-03)

The evidence behind [m13-implementation-plan.md](../../specs/m13-implementation-plan.md).
Produced in the M13 design session; **no code was changed and no statistical
number moved** in this directory's making.

## Why the raw agent output is kept verbatim

`statistics-changes.md` §0 makes the blind re-derivation a **step of the
process**, not a note: step 3 produces an independent "what the textbook-correct
method is", and step 4 arbitrates it against the legacy implementation. A summary
alone would make two things unverifiable — that the derivation actually says what
the plan claims, and that it was blind. Both matter more than tidiness, so the
JSON is committed as delivered.

| File | What it settles |
|---|---|
| [code-audit.md](code-audit.md) | The as-built half. Every claim anchored to a line, or derived in place. §7 is why uniform ddof was dropped; §8 prices the correction WP. |
| [relative-effect.derivation.json](relative-effect.derivation.json) | The CI for a ratio of two means. Recommends Fieller; the finding that drove D10 is that Fieller's rejection set at θ=0 is *identical* to today's shortcut. |
| [multiplicity.derivation.json](multiplicity.derivation.json) | The two-tier FWER question. Worst case exactly `2α` flat in `g` and `k`; the current levels are a valid gatekeeping procedure with the gate unenforced; and the proof that **no fixed per-comparison level reproduces Holm**. |
| [proportion-interval.derivation.json](proportion-interval.derivation.json) | The proportion interval and the pooled/unpooled question, which turned out to be one question. Recommends Miettinen–Nurminen: one score statistic used three ways. |

## Blindness, and its documented limit

Each agent was forbidden the repository and forbidden to execute code. Neither
opened a project file — verified against the agent transcripts, not taken on
trust.

**But an agent inside this tree is never fully blind, and that is a property of
the harness, not of the agents.** `CLAUDE.md` and `.claude/rules/*.md` are
auto-injected into every subagent's context before its task begins, so the
as-built architecture — module names, "legacy two-tier Bonferroni keyed off
`is_main_metric`", "BH is read-time" — arrives whether or not it is wanted. No
prompt-level file ban can prevent it.

Two consequences, both applied in the plan:

1. **Each derivation discloses what leaked** and where it touched the reasoning.
   The proportion-interval agent names four specific injected facts and flags
   each use; the multiplicity agent opens with its disclosure unprompted. Read
   those fields before weighting a conclusion.
2. **Agreement between a derivation and the code audit is corroboration, not
   independent confirmation.** Where both say the same thing — notably that BH
   already lets a decision and its interval diverge — the audit is the primary
   source and the derivation may be an echo.

To run a genuinely blind derivation, the agent must run **outside this working
tree**.

## The methodological result worth carrying forward

Three separate times, the project's own arbitration instrument turned out to be
blind to the change it was being asked to certify:

- the relative-effect shortcut — rejection set identical at the null, so the A/A
  FPRs must agree *to the last false positive*;
- uniform ddof — the effect is below the matrix's noise floor by two to three
  orders of magnitude;
- pooled → score for proportions — the FPR difference is second-order because the
  two tails shift in opposite directions and cancel.

Hence D6: where `abk validate` cannot arbitrate, the plan says so in
`statistics-changes.md` rather than running a sweep that cannot answer. In the
proportion derivation's words — *a calibration that cannot see the change it is
being asked to certify is worse than no calibration.*
