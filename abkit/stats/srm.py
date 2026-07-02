"""Sample-ratio-mismatch (SRM) chi-square gate.

The A/B data-integrity check detectkit has no analog for (architecture §5 step 4):
observed per-variant unit counts vs the declared ``expected_split``, checked BEFORE
any effect is computed. Failure is blocking-but-non-dropping — the pipeline still
writes the row with ``srm_flag=1`` and surfaces a loud red gate; it never silently
drops results.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import scipy.stats as sps

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

    def describe(self) -> str:
        """The loud one-liner for the CLI gate (data-contract-and-reporting.md §6)."""
        total = sum(self.observed.values())
        observed_shares = "/".join(f"{self.observed[v] / total:.2f}" for v in sorted(self.observed))
        expected_shares = "/".join(
            f"{self.expected_share[v]:.2f}" for v in sorted(self.expected_share)
        )
        if self.srm_flag:
            return (
                f"SRM FAILED (observed {observed_shares} vs expected {expected_shares}, "
                f"chi2 p={self.pvalue:.2g}) — effects untrustworthy"
            )
        return f"SRM ok (observed {observed_shares} vs expected {expected_shares}, chi2 p={self.pvalue:.2g})"


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
    )
