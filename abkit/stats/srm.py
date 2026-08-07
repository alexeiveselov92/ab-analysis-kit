"""Sample-ratio-mismatch (SRM) gate.

The A/B data-integrity check detectkit has no analog for (architecture §5 step 4):
observed per-variant unit counts vs the declared ``expected_split``, checked BEFORE
any effect is computed. Failure is blocking-but-non-dropping — the pipeline still
writes the row with ``srm_flag=1`` and surfaces a loud red gate; it never silently
drops results.

Two gates, dispatched by cadence (data-contract-and-reporting.md §6,
cumulative-intervals.md §6.5):

- **daily & coarser** — :func:`srm_check`, a chi-square goodness-of-fit at the
  strict ``DEFAULT_SRM_ALPHA``. A bounded daily look count on a 3.3σ hard gate
  makes the peeking inflation negligible, so no anytime correction is needed.
- **sub-day** (``cadence < 1d``) — :func:`sequential_multinomial_srm`, an
  anytime-valid Dirichlet-multinomial e-process (Lindon & Malek 2022) that is
  valid at EVERY look by construction. A dense sub-day cadence would peek the
  chi-square hard gate dozens of times a day → false alarms; the e-process is
  the honest fix (statistics-changes.md §4.2).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import scipy.stats as sps
from scipy.special import gammaln

from abkit.stats.exceptions import SampleValidationError

#: SRM must be much stricter than the experiment alpha: a false SRM alarm is cheap,
#: a missed randomisation failure poisons every effect. 0.001 is the accepted default.
DEFAULT_SRM_ALPHA = 0.001


@dataclass(frozen=True)
class SrmResult:
    pvalue: float
    srm_flag: bool
    alpha: float
    observed: dict[str, int] = field(default_factory=dict)
    expected_share: dict[str, float] = field(default_factory=dict)
    #: which gate produced this verdict — ``"chi2"`` (daily) or
    #: ``"sequential_multinomial"`` (sub-day anytime-valid).
    kind: str = "chi2"
    #: the anytime-valid e-value (running max over looks) for the sequential
    #: gate; ``None`` for chi-square. ``pvalue`` is its dual: ``min(1, 1/e_value)``.
    e_value: float | None = None

    def culprit(self) -> tuple[str, float] | None:
        """The arm contributing most to the mismatch (m14 DEC-5(c))."""
        return srm_culprit(self.observed, self.expected_share)

    def describe(self) -> str:
        """The loud one-liner for the CLI gate (data-contract-and-reporting.md §6)."""
        total = sum(self.observed.values())
        observed_shares = (
            "/".join(f"{self.observed[v] / total:.2f}" for v in sorted(self.observed))
            if total
            else "n/a"
        )
        expected_shares = "/".join(
            f"{self.expected_share[v]:.2f}" for v in sorted(self.expected_share)
        )
        if self.kind == "sequential_multinomial":
            evid = f"anytime e={self.e_value:.3g} p={self.pvalue:.2g}"
        else:
            evid = f"chi2 p={self.pvalue:.2g}"
        if self.srm_flag:
            # m14 DEC-5(c): at 3+ arms name WHERE the mismatch is concentrated.
            # "assignment is broken" without "which arm" is a diagnosis the
            # operator cannot act on. Silent at two arms, where the residuals
            # are mirror images and naming one is a tautology — so a two-arm
            # gate line is `0.8.0`'s to the character.
            blame = ""
            # Only under the chi-square gate. The sub-day e-process's flag is a
            # RUNNING MAX over earlier looks, so a since-rebalanced cohort reads
            # FAILED on an even split — and a decomposition of the CURRENT
            # counts would then blame an arm at noise magnitude, in the opposite
            # direction, as the diagnosis (review finding).
            if self.kind == "chi2" and len(self.observed) > 2:
                found = self.culprit()
                if found is not None:
                    arm, residual = found
                    side = "too few" if residual < 0 else "too many"
                    blame = f" — {arm} contributes most to the mismatch ({side} units)"
            return (
                f"SRM FAILED (observed {observed_shares} vs expected {expected_shares}, "
                f"{evid}){blame} — effects untrustworthy"
            )
        return f"SRM ok (observed {observed_shares} vs expected {expected_shares}, {evid})"


def srm_check(
    observed_counts: Mapping[str, int],
    expected_split: Mapping[str, float],
    alpha: float = DEFAULT_SRM_ALPHA,
) -> SrmResult:
    """Chi-square goodness-of-fit of observed variant counts vs the expected split."""
    if set(observed_counts) != set(expected_split):
        raise SampleValidationError(
            f"observed variants {sorted(observed_counts)} != expected_split variants {sorted(expected_split)}"
        )
    if len(observed_counts) < 2:
        raise SampleValidationError("SRM check requires at least two variants")

    variants = sorted(observed_counts)
    counts = np.array([observed_counts[v] for v in variants], dtype=np.float64)
    shares = np.array([expected_split[v] for v in variants], dtype=np.float64)
    if np.any(counts < 0):
        raise SampleValidationError("observed counts must be non-negative")
    total = counts.sum()
    if total <= 0:
        raise SampleValidationError("observed counts must not all be zero")
    if np.any(shares <= 0):
        raise SampleValidationError("expected_split shares must be positive")
    shares = shares / shares.sum()

    _, pvalue = sps.chisquare(f_obs=counts, f_exp=total * shares)
    return SrmResult(
        pvalue=float(pvalue),
        srm_flag=bool(pvalue < alpha),
        alpha=alpha,
        observed={v: int(observed_counts[v]) for v in variants},
        expected_share={v: float(share) for v, share in zip(variants, shares, strict=True)},
        kind="chi2",
    )


def srm_culprit(
    observed_counts: Mapping[str, int],
    expected_share: Mapping[str, float],
) -> tuple[str, float] | None:
    """Which arm contributes most to the SRM chi-square (m14 DEC-5(c)).

    A DECOMPOSITION of a statistic already computed, never a second gate: the
    joint K-way test keeps deciding, and this only says where the mismatch is
    concentrated. Returns ``(arm, residual)`` — the Pearson residual
    ``(observed − expected) / √expected``, signed, so the surface can say "too
    few" or "too many" — or ``None`` when the decomposition is not defined
    (fewer than two arms, no units, a non-positive expectation).

    **It is the chi-square CONTRIBUTION, not a z-score**, and no surface prints
    it as one: a Pearson residual's null standard deviation is ``√(1−p)``, not 1
    (measured 0.70 / 0.82 / 0.90 at 2 / 3 / 5 even arms), so a "σ" label would
    overstate it by up to 30%. The ~N(0,1) quantity is the ADJUSTED residual
    ``(o−e)/√(e(1−p))``, and under a strongly uneven declared split the two can
    name DIFFERENT arms (measured: ~14% of imbalanced draws). This answers the
    question the gate asks — which arm drives the statistic that failed — and
    the adjusted residual is a named follow-up, not a silent substitution.

    At 3+ arms "assignment is broken" without "which arm" is a diagnosis the
    operator cannot act on; at two arms the answer is a tautology (the residuals
    are mirror images), which is why every surface gates the line on the arm
    count rather than printing it always.
    """
    if len(observed_counts) < 2 or set(observed_counts) != set(expected_share):
        return None
    variants = sorted(observed_counts)
    counts = np.array([float(observed_counts[v]) for v in variants], dtype=np.float64)
    shares = np.array([float(expected_share[v]) for v in variants], dtype=np.float64)
    total = counts.sum()
    share_total = shares.sum()
    if total <= 0 or share_total <= 0 or np.any(shares <= 0):
        return None
    expected = total * (shares / share_total)
    residuals = (counts - expected) / np.sqrt(expected)
    idx = int(np.argmax(np.abs(residuals)))
    return variants[idx], float(residuals[idx])


def sequential_multinomial_srm(
    counts_stream: Sequence[Mapping[str, int]],
    expected_split: Mapping[str, float],
    prior: Mapping[str, float] | None = None,
    alpha: float = DEFAULT_SRM_ALPHA,
) -> list[SrmResult]:
    """Anytime-valid sub-day SRM via a Dirichlet-multinomial mixture e-process.

    Lindon & Malek, *Anytime-Valid Inference for Multinomial Count Data*
    (NeurIPS 2022, arXiv:2011.03567 §2.2). The null ``M0`` is iid
    ``Multinomial(1, θ0)`` (``θ0 = expected_split``); the alternative ``M1``
    mixes ``θ ~ Dirichlet(α0)``. By conjugacy the Bayes factor at cumulative
    counts ``S = (S₁,…,S_d)`` is closed-form and depends on the data ONLY
    through ``S`` (arrival order is irrelevant — so a stream of *cumulative*
    per-variant count vectors is the exact input)::

        BF₁₀ = Beta(α0 + S) / Beta(α0) · 1 / θ0^S

    computed in log space with ``gammaln`` (never factorials — they overflow at
    A/B N). With ``A0 = Σ α0,ᵢ`` and ``N = Σ Sᵢ``::

        log BF = gammaln(A0) − gammaln(A0 + N)
                 + Σᵢ [ gammaln(α0,ᵢ + Sᵢ) − gammaln(α0,ᵢ) − Sᵢ·log(θ0,ᵢ) ]

    ``{BFₙ}`` is a non-negative martingale under ``M0`` with ``BF₀ = 1``, so by
    Ville's inequality ``P(supₙ BFₙ ≥ 1/α) ≤ α`` over ANY data-dependent look
    schedule — the anytime-valid guarantee. The per-look verdict is therefore
    the RUNNING maximum e-value; the anytime p-value is its dual
    ``pₙ = min(1, 1/ supₖ≤ₙ BFₖ)`` (non-increasing, so once the gate trips it
    stays tripped). The guarantee is asymptotic-free and holds for ANY fixed
    positive prior — only the stopping time (power) depends on ``α0``; the prior
    must be fixed in advance, not tuned to the data (statistics-changes.md §4.2).

    Args:
        counts_stream: cumulative per-variant unit counts at each look, ascending
            (``[{variant: count}, …]``); every dict must key exactly the
            ``expected_split`` variants (the pipeline zero-fills absent arms).
        expected_split: the null multinomial ``θ0`` (weights; normalised here).
        prior: per-variant Dirichlet concentration ``α0``. ``None`` ⇒ the paper's
            named default, a uniform ``Dir(1,…,1)``. An explicit map (e.g.
            ``k·θ0`` for a mean-pinned concentration) trades power across the
            departure size — correctness is unchanged.
        alpha: the sequential level; the gate trips when ``e_value ≥ 1/alpha``.
            Defaults to :data:`DEFAULT_SRM_ALPHA` (the same strict gate as χ²).

    Returns:
        One :class:`SrmResult` per look (``kind="sequential_multinomial"``),
        carrying the running e-value, its dual anytime p-value, the cumulative
        counts, and the trip flag. Empty stream ⇒ ``[]``.
    """
    variants = sorted(expected_split)
    if len(variants) < 2:
        raise SampleValidationError("SRM check requires at least two variants")
    shares = np.array([expected_split[v] for v in variants], dtype=np.float64)
    if np.any(shares <= 0):
        raise SampleValidationError("expected_split shares must be positive")
    shares = shares / shares.sum()

    if prior is None:
        # the paper's named default: a uniform Dirichlet, α0,ᵢ = 1 (Beta(1,1)
        # for two variants). No magic concentration constant is invented.
        alpha0 = np.ones(len(variants), dtype=np.float64)
    else:
        if set(prior) != set(variants):
            raise SampleValidationError(
                f"prior variants {sorted(prior)} != expected_split variants {variants}"
            )
        alpha0 = np.array([prior[v] for v in variants], dtype=np.float64)
        if np.any(alpha0 <= 0):
            raise SampleValidationError("prior concentrations must be positive")

    log_shares = np.log(shares)
    a0_total = float(alpha0.sum())
    gammaln_a0_total = gammaln(a0_total)
    gammaln_alpha0 = gammaln(alpha0)
    log_reject = -math.log(alpha)  # reject when log BF ≥ log(1/alpha)

    results: list[SrmResult] = []
    running_max_log_bf = -math.inf
    expected_share = {v: float(s) for v, s in zip(variants, shares, strict=True)}
    for look in counts_stream:
        if set(look) != set(variants):
            raise SampleValidationError(
                f"look counts {sorted(look)} != expected_split variants {variants}"
            )
        counts = np.array([look[v] for v in variants], dtype=np.float64)
        if np.any(counts < 0):
            raise SampleValidationError("observed counts must be non-negative")
        n_total = float(counts.sum())
        log_bf = float(
            gammaln_a0_total
            - gammaln(a0_total + n_total)
            + np.sum(gammaln(alpha0 + counts) - gammaln_alpha0 - counts * log_shares)
        )
        running_max_log_bf = max(running_max_log_bf, log_bf)
        # e-value ≥ 1 always (N=0 ⇒ BF=1); p = 1/sup(e), clipped to 1.
        pvalue = math.exp(-max(running_max_log_bf, 0.0))
        e_value = math.inf if running_max_log_bf > 709.0 else math.exp(running_max_log_bf)
        results.append(
            SrmResult(
                pvalue=float(pvalue),
                srm_flag=bool(running_max_log_bf >= log_reject),
                alpha=alpha,
                observed={v: int(look[v]) for v in variants},
                expected_share=expected_share,
                kind="sequential_multinomial",
                e_value=float(e_value),
            )
        )
    return results
