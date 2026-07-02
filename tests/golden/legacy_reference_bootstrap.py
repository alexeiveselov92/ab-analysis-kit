"""Legacy bootstrap MATH transcribed from docs/reference/legacy-method-catalogue.md.

Golden strategy (docs/specs/statistics-changes.md H1/H2): the legacy engine seeded
the GLOBAL numpy RNG, so the new engine's random stream deliberately differs and an
output-vs-output comparison against the legacy code is impossible. Parity is proven
by SHARING the stream instead: this module reuses the engine's single tiny draw
helper (:func:`abkit.stats.bootstrap.engine.draw_stratum_indices`) and the
documented draw order (quanta of ``BLOCK_QUANTUM`` replicates, strata in
``np.unique`` order, variant 1 fully before variant 2, ONE draw per stratum shared
by paired arms / covariate channels) with an equal seed, so both sides consume
identical index and weight matrices. EVERYTHING downstream is transcribed
independently from the catalogue:

- statistic application via ``np.apply_along_axis`` (legacy ``StatFuncApplier``);
- ``boot_data = boot_2 − boot_1`` (absolute) / ``boot_2/boot_1 − 1`` (relative);
- percentile CI ``np.quantile(boot, [α/2, 1−α/2])``;
- sign p-value ``2·min(mean(boot>0), mean(boot<0))``;
- post-normed relative ``(S2/S1)/(S2_cov/S1_cov) − 1`` (catalogue
  "PostNormedBootstrapTest");
- Poisson dot-product means ``np.dot(weights, array)/weights.sum(axis=1)``
  (catalogue "PoissonBootstrapTest");
- z-score standardisation ``(boot_i − mean(boot_i))/std(boot_i)`` for the paired
  post-normed absolute branch (catalogue "PairedPostNormedBootstrapTest");
- per-method effect: real-data point estimate for the plain/Poisson/post-normed
  tests, ``mean(boot_data)`` for the paired variants (the legacy inconsistency H9);
- stratified counts: per-group shares ``count/n`` pooled via min/mean, normalised
  to sum 1, scaled by each group's ``n`` and ``int()``-truncated (catalogue
  "Vectorised sampling engine") — the golden fixtures choose mixes where this
  coincides with the new engine's Hamilton apportionment (H6).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from abkit.stats.bootstrap.engine import BLOCK_QUANTUM, draw_stratum_indices
from abkit.stats.samples import FloatArray

StatFunc = Callable[[FloatArray], float]
CategoryArray = np.ndarray


def _resample_matrices(
    rng: np.random.Generator,
    strata_channels: Sequence[tuple[FloatArray, ...]],
    counts: Sequence[int],
    n_samples: int,
) -> list[FloatArray]:
    """Materialise full resample matrices, drawing in the engine's quantum order.

    Only the DRAWS follow the new engine's contract (quanta of ``BLOCK_QUANTUM``
    rows, strata in the given order, one shared index matrix per stratum across
    channels); the fancy-indexed fill is the legacy ``values[indices]`` semantics
    (catalogue "Vectorised sampling engine").
    """
    n_channels = len(strata_channels[0])
    width = int(sum(counts))
    matrices = [np.empty((n_samples, width), dtype=np.float64) for _ in range(n_channels)]
    produced = 0
    while produced < n_samples:
        rows = min(BLOCK_QUANTUM, n_samples - produced)
        offset = 0
        for channels, count in zip(strata_channels, counts, strict=True):
            indices = draw_stratum_indices(rng, int(channels[0].size), rows, int(count))
            for matrix, values in zip(matrices, channels, strict=True):
                matrix[produced : produced + rows, offset : offset + count] = values[indices]
            offset += int(count)
        produced += rows
    return matrices


def _legacy_stat(matrix: FloatArray, stat_func: StatFunc) -> FloatArray:
    """Legacy ``StatFuncApplier``: ``np.apply_along_axis(stat_func, 1, matrix)``."""
    return np.apply_along_axis(stat_func, 1, matrix)


def _boot_data(boot_1: FloatArray, boot_2: FloatArray, test_type: str) -> FloatArray:
    """Legacy effect distribution (catalogue "BootstrapTest" key formula)."""
    if test_type == "absolute":
        return boot_2 - boot_1
    return boot_2 / boot_1 - 1.0


def _legacy_outputs(boot_data: FloatArray, alpha: float) -> dict[str, float]:
    """Legacy common result computation: percentile CI + sign p-value (baseline §4)."""
    left_bound, right_bound = np.quantile(boot_data, [alpha / 2.0, 1.0 - alpha / 2.0])
    pvalue = 2.0 * min(float(np.mean(boot_data > 0)), float(np.mean(boot_data < 0)))
    return {
        "left_bound": float(left_bound),
        "right_bound": float(right_bound),
        "ci_length": float(right_bound) - float(left_bound),
        "pvalue": float(pvalue),
        "boot_mean": float(np.mean(boot_data)),
    }


def _legacy_pooled_weights(
    categories_1: CategoryArray,
    categories_2: CategoryArray,
    n_1: int,
    n_2: int,
    weight_method: str,
) -> tuple[CategoryArray, FloatArray]:
    """Group-pooled, normalised stratum weights (catalogue "Vectorised sampling engine").

    Per-group weights are the observed shares ``count / n``; pooled elementwise by
    ``weight_method`` ("min"/"mean") and normalised to sum 1.
    """
    order = np.unique(categories_1)
    _, counts_1 = np.unique(categories_1, return_counts=True)
    _, counts_2 = np.unique(categories_2, return_counts=True)
    shares_1 = counts_1 / n_1
    shares_2 = counts_2 / n_2
    if weight_method == "min":
        pooled = np.minimum(shares_1, shares_2)
    else:
        pooled = (shares_1 + shares_2) / 2.0
    return order, pooled / pooled.sum()


def _stratum_layout(
    values: FloatArray,
    cov_values: FloatArray | None,
    categories: CategoryArray | None,
    order: CategoryArray | None,
    weights: FloatArray | None,
) -> tuple[list[tuple[FloatArray, ...]], list[int]]:
    """One variant's strata channels + legacy ``int()``-truncated resample counts."""
    if categories is None:
        channels = (values,) if cov_values is None else (values, cov_values)
        return [channels], [int(values.size)]
    assert order is not None and weights is not None
    strata: list[tuple[FloatArray, ...]] = []
    counts: list[int] = []
    for category, weight in zip(order, weights, strict=True):
        mask = categories == category
        channels = (values[mask],) if cov_values is None else (values[mask], cov_values[mask])
        strata.append(channels)
        counts.append(int(values.size * float(weight)))  # legacy int() truncation
    return strata, counts


def legacy_bootstrap(
    values_1: FloatArray,
    values_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    stat_func: StatFunc = np.mean,
    test_type: str = "relative",
    categories_1: CategoryArray | None = None,
    categories_2: CategoryArray | None = None,
    weight_method: str = "min",
) -> dict[str, float]:
    """Catalogue "BootstrapTest": independent (optionally stratified) resampling.

    Effect = the real-data point estimate ``stat_2 − stat_1`` / ``stat_2/stat_1 − 1``.
    """
    rng = np.random.default_rng(seed)
    order = weights = None
    if categories_1 is not None:
        assert categories_2 is not None
        order, weights = _legacy_pooled_weights(
            categories_1, categories_2, int(values_1.size), int(values_2.size), weight_method
        )
    strata_1, counts_1 = _stratum_layout(values_1, None, categories_1, order, weights)
    strata_2, counts_2 = _stratum_layout(values_2, None, categories_2, order, weights)
    (matrix_1,) = _resample_matrices(rng, strata_1, counts_1, n_samples)
    (matrix_2,) = _resample_matrices(rng, strata_2, counts_2, n_samples)
    boot_data = _boot_data(
        _legacy_stat(matrix_1, stat_func), _legacy_stat(matrix_2, stat_func), test_type
    )
    outputs = _legacy_outputs(boot_data, alpha)
    stat_1, stat_2 = float(stat_func(values_1)), float(stat_func(values_2))
    outputs["effect"] = stat_2 - stat_1 if test_type == "absolute" else stat_2 / stat_1 - 1.0
    return outputs


def legacy_paired_bootstrap(
    values_1: FloatArray,
    values_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    stat_func: StatFunc = np.mean,
    test_type: str = "relative",
) -> dict[str, float]:
    """Catalogue "PairedBootstrapTest": one index stream for both aligned arms.

    Legacy quirk preserved here: ``effect = mean(boot_data)`` (NOT the point
    estimate — the H9 inconsistency the new engine deliberately fixes).
    """
    rng = np.random.default_rng(seed)
    matrix_1, matrix_2 = _resample_matrices(
        rng, [(values_1, values_2)], [int(values_1.size)], n_samples
    )
    boot_data = _boot_data(
        _legacy_stat(matrix_1, stat_func), _legacy_stat(matrix_2, stat_func), test_type
    )
    outputs = _legacy_outputs(boot_data, alpha)
    outputs["effect"] = float(np.mean(boot_data))
    return outputs


def _poisson_replicate_means(
    rng: np.random.Generator, arrays: Sequence[FloatArray], n_samples: int
) -> list[FloatArray]:
    """Poisson dot-product means, drawn in the engine's quantum order.

    Legacy math (catalogue "PoissonBootstrapTest" key formula):
    ``weights = poisson(1, (n_samples, n)); np.dot(weights, array)/weights.sum(axis=1)``.
    """
    n_units = int(arrays[0].size)
    outputs = [np.empty(n_samples, dtype=np.float64) for _ in arrays]
    produced = 0
    while produced < n_samples:
        rows = min(BLOCK_QUANTUM, n_samples - produced)
        weights = rng.poisson(1.0, size=(rows, n_units))
        weight_sums = weights.sum(axis=1)
        for output, array in zip(outputs, arrays, strict=True):
            output[produced : produced + rows] = np.dot(weights, array) / weight_sums
        produced += rows
    return outputs


def legacy_poisson_bootstrap(
    values_1: FloatArray,
    values_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    test_type: str = "relative",
) -> dict[str, float]:
    """Catalogue "PoissonBootstrapTest": independent Poisson-weighted means.

    Variant 1's weight stream is drawn fully before variant 2's (draw order).
    Effect = the real-data point estimate on the means.
    """
    rng = np.random.default_rng(seed)
    (stat_1,) = _poisson_replicate_means(rng, [values_1], n_samples)
    (stat_2,) = _poisson_replicate_means(rng, [values_2], n_samples)
    boot_data = _boot_data(stat_1, stat_2, test_type)
    outputs = _legacy_outputs(boot_data, alpha)
    mean_1, mean_2 = float(np.mean(values_1)), float(np.mean(values_2))
    outputs["effect"] = mean_2 - mean_1 if test_type == "absolute" else mean_2 / mean_1 - 1.0
    return outputs


def legacy_paired_poisson_bootstrap(
    values_1: FloatArray,
    values_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    test_type: str = "relative",
) -> dict[str, float]:
    """Catalogue "PairedPoissonBootstrapTest": ONE weights matrix for both arms."""
    rng = np.random.default_rng(seed)
    stat_1, stat_2 = _poisson_replicate_means(rng, [values_1, values_2], n_samples)
    boot_data = _boot_data(stat_1, stat_2, test_type)
    outputs = _legacy_outputs(boot_data, alpha)
    mean_1, mean_2 = float(np.mean(values_1)), float(np.mean(values_2))
    outputs["effect"] = mean_2 - mean_1 if test_type == "absolute" else mean_2 / mean_1 - 1.0
    return outputs


def legacy_post_normed_bootstrap(
    values_1: FloatArray,
    cov_1: FloatArray,
    values_2: FloatArray,
    cov_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    stat_func: StatFunc = np.mean,
) -> dict[str, float]:
    """Catalogue "PostNormedBootstrapTest", RELATIVE branch (the reproduced one).

    Value and covariate share the same index matrix per variant (cov_bootstrap);
    ``boot_data = (S2/S1)/(S2_cov/S1_cov) − 1``; effect via the same formula on
    the original point statistics.
    """
    rng = np.random.default_rng(seed)
    matrix_1, cov_matrix_1 = _resample_matrices(
        rng, [(values_1, cov_1)], [int(values_1.size)], n_samples
    )
    matrix_2, cov_matrix_2 = _resample_matrices(
        rng, [(values_2, cov_2)], [int(values_2.size)], n_samples
    )
    s_1 = _legacy_stat(matrix_1, stat_func)
    s_2 = _legacy_stat(matrix_2, stat_func)
    s_1_cov = _legacy_stat(cov_matrix_1, stat_func)
    s_2_cov = _legacy_stat(cov_matrix_2, stat_func)
    boot_data = (s_2 / s_1) / (s_2_cov / s_1_cov) - 1.0
    outputs = _legacy_outputs(boot_data, alpha)
    outputs["effect"] = (float(stat_func(values_2)) / float(stat_func(values_1))) / (
        float(stat_func(cov_2)) / float(stat_func(cov_1))
    ) - 1.0
    return outputs


def legacy_paired_post_normed_bootstrap(
    values_1: FloatArray,
    values_2: FloatArray,
    *,
    seed: int,
    n_samples: int,
    alpha: float,
    stat_func: StatFunc = np.mean,
) -> dict[str, float]:
    """Catalogue "PairedPostNormedBootstrapTest", ABSOLUTE branch (the reproduced one).

    Paired index draws; each arm's bootstrap distribution is z-score standardised
    ``(boot_i − mean(boot_i)) / std(boot_i)`` (np.std, ddof=0), then differenced.
    ``effect = mean(boot_data)``. The covariate is required by legacy validation
    but never used in the math.
    """
    rng = np.random.default_rng(seed)
    matrix_1, matrix_2 = _resample_matrices(
        rng, [(values_1, values_2)], [int(values_1.size)], n_samples
    )
    boot_1 = _legacy_stat(matrix_1, stat_func)
    boot_2 = _legacy_stat(matrix_2, stat_func)
    normed_1 = (boot_1 - np.mean(boot_1)) / np.std(boot_1)
    normed_2 = (boot_2 - np.mean(boot_2)) / np.std(boot_2)
    boot_data = normed_2 - normed_1
    outputs = _legacy_outputs(boot_data, alpha)
    outputs["effect"] = float(np.mean(boot_data))
    return outputs
