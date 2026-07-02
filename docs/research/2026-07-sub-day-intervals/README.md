# Research: sub-day cumulative intervals (2026-07)

Three independent research reports (statistical honesty / warehouse engineering /
config-DX + competitor scan) behind the **abk-intervals decision** recorded in
[`docs/specs/cumulative-intervals.md` §6](../../specs/cumulative-intervals.md).

Each report carries its own *unverified-claims register* — several primary
sources returned 403 through the research proxy, so specific numbers flagged
there must be re-verified before being quoted in user-facing docs.

Decision summary: duration/schedule-typed `cadence` with no hard time floor
(`max_looks` is the hard gate), `data_lag` completeness watermark, exclusive UTC
`end_ts` window contract, monitoring-mode posture for fixed-horizon sub-day
grids with `sequential: always_valid` as the sanctioned early-decision path,
small-n demotion, anytime-valid sequential SRM below 1d, day-grained unit state
with current-day tail reads, and the CUPED covariate window resolved to a fixed
whole-day lookback.
