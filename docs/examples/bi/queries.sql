-- ab-analysis-kit — BI reference queries against `_ab_results`
-- ============================================================================
-- `_ab_results` is abkit's stable, BI-friendly contract table (data-contract-and-
-- reporting.md §2–§3). abkit owns the numbers; point Grafana / Lightdash / Metabase
-- / Superset at this table with the recipes below. Copy a block, set the
-- :experiment / :metric parameters your tool uses, and go.
--
-- WRITTEN FOR CLICKHOUSE (abkit's primary backend). Portability:
--   * ClickHouse: the table is a ReplacingMergeTree(created_at) — you MUST read it
--     `FINAL` (or dedup by the latest `created_at`) or you will see stale duplicate
--     versions of a recomputed row. Every recipe below uses FINAL.
--   * PostgreSQL / MySQL: abkit upserts on the primary key, so the base table already
--     holds one row per series point — DELETE the `FINAL` keyword and the queries work
--     unchanged.
--
-- The series key (one stabilization point) is:
--   (experiment, metric, method_config_id, name_1, name_2, end_ts)
-- name_1 = control arm, name_2 = treatment arm. A comparison is one (experiment,
-- metric, method_config_id, name_1, name_2); its points are ordered by end_ts.
--
-- INVARIANTS BAKED INTO EVERY RECIPE (do not drop them — see README.md):
--   1. Read FINAL (ClickHouse) so recomputed rows don't double-count.
--   2. Group/filter by method_config_id — >1 id per (experiment, metric) is a config
--      drift that draws duplicate stabilization lines (run `abk clean`). See recipe 8.
--   3. Compare p-value to the ROW'S effective `alpha`, never a hardcoded 0.05 — abkit
--      applies a two-tier (main vs secondary) Bonferroni correction per row.
--   4. Pre-horizon fixed-horizon CIs are NOT peeking-valid. Only trust a decision at
--      `is_horizon = true`, or when `ci_kind = 'always_valid'` (a peeking-safe
--      confidence sequence). Recipes surface both flags; don't stop early on a fixed
--      pre-horizon CI (that is exactly the optional-stopping error `abk validate` exists
--      to expose).
--   5. Effect / CI / p-value / std columns are NULLABLE (a row demoted by
--      `insufficient_data`, or blocked by SRM, has NULLs) — never assume non-null.
--
-- Set these to your tool's parameter syntax (shown here as ClickHouse {name:Type}):
--   {experiment:String}   e.g. 'example_signup_test'
--   {metric:String}       e.g. 'example_signup_cr'
-- Replace `abkit_internal._ab_results` with your project's internal database/schema.


-- ============================================================================
-- RECIPE 1 — Latest result per comparison (the headline table / "current state")
-- ----------------------------------------------------------------------------
-- One row per (experiment, metric, method, arm-pair) at its most recent cutoff:
-- the effect, its CI, whether it is significant AT THE ROW'S OWN ALPHA, and a
-- derived verdict. This is your at-a-glance experiment scoreboard.
-- ============================================================================
WITH latest AS (
    SELECT
        experiment, metric, method_config_id, name_1, name_2,
        argMax(effect,            end_ts) AS effect,
        argMax(left_bound,        end_ts) AS left_bound,
        argMax(right_bound,       end_ts) AS right_bound,
        argMax(pvalue,            end_ts) AS pvalue,
        argMax(alpha,             end_ts) AS alpha,          -- effective, two-tier
        argMax(reject,            end_ts) AS reject,
        argMax(is_main_metric,    end_ts) AS is_main_metric,
        argMax(is_guardrail,      end_ts) AS is_guardrail,
        argMax(size_1,            end_ts) AS size_1,
        argMax(size_2,            end_ts) AS size_2,
        argMax(srm_flag,          end_ts) AS srm_flag,
        argMax(decision_blocked,  end_ts) AS decision_blocked,
        argMax(insufficient_data, end_ts) AS insufficient_data,
        argMax(is_horizon,        end_ts) AS is_horizon,
        argMax(ci_kind,           end_ts) AS ci_kind,
        max(end_ts)                       AS as_of
    FROM abkit_internal._ab_results FINAL
    GROUP BY experiment, metric, method_config_id, name_1, name_2
)
SELECT
    experiment, metric, name_1 AS control, name_2 AS treatment,
    round(effect, 4)            AS effect,
    round(left_bound, 4)        AS ci_low,
    round(right_bound, 4)       AS ci_high,
    round(pvalue, 4)            AS pvalue,
    round(alpha, 4)             AS alpha_effective,
    size_1, size_2, as_of, ci_kind, is_horizon,
    -- Derived verdict. Decision-readiness = at horizon OR always-valid (peeking-safe).
    multiIf(
        decision_blocked OR srm_flag, 'BLOCKED (SRM)',
        insufficient_data,           'INSUFFICIENT',
        NOT (is_horizon OR ci_kind = 'always_valid'), 'PENDING (pre-horizon, not peeking-valid)',
        reject AND effect > 0,       'WIN',
        reject AND effect < 0,       'LOSE',
        NOT reject,                  'FLAT',
        'INCONCLUSIVE'
    ) AS verdict
FROM latest
WHERE experiment = {experiment:String}
ORDER BY is_main_metric DESC, metric, control, treatment;


-- ============================================================================
-- RECIPE 2 — Effect + CI band over time (the signature stabilization chart)
-- ----------------------------------------------------------------------------
-- Plot `effect` as the line and (ci_low, ci_high) as a band vs `end_ts`; draw a
-- zero reference line. The band narrowing toward the horizon is the story. Use
-- `is_horizon` to mark the decision point and `ci_kind` to distinguish fixed
-- (not peeking-valid before the horizon) from always-valid CIs.
-- ============================================================================
SELECT
    end_ts                                   AS t,
    effect,
    left_bound                               AS ci_low,
    right_bound                              AS ci_high,
    ci_kind,                                  -- 'fixed' | 'always_valid'
    is_horizon,                              -- the planned decision cutoff
    size_1 + size_2                          AS n_total
FROM abkit_internal._ab_results FINAL
WHERE experiment = {experiment:String}
  AND metric     = {metric:String}
  AND effect IS NOT NULL                     -- skip demoted/blocked points
ORDER BY method_config_id, name_1, name_2, end_ts;
-- If this returns duplicate lines, you have >1 method_config_id — see recipe 8.


-- ============================================================================
-- RECIPE 3 — Raw per-arm values + spread + CUPED over time
-- ----------------------------------------------------------------------------
-- The underlying arm means, their std, and (when CUPED is used) the covariate-
-- adjusted means. `value_*` are per-unit means; `cov_value_*` are the CUPED-
-- adjusted arm values (NULL when the method is not CUPED).
-- ============================================================================
SELECT
    end_ts                 AS t,
    name_1 AS control, name_2 AS treatment,
    value_1 AS control_mean, value_2 AS treatment_mean,
    std_1   AS control_std,  std_2   AS treatment_std,
    cov_value_1 AS control_cuped, cov_value_2 AS treatment_cuped,  -- NULL if not CUPED
    size_1, size_2
FROM abkit_internal._ab_results FINAL
WHERE experiment = {experiment:String}
  AND metric     = {metric:String}
ORDER BY method_config_id, name_1, name_2, end_ts;


-- ============================================================================
-- RECIPE 4 — Significance vs the ROW'S effective alpha (two-tier aware)
-- ----------------------------------------------------------------------------
-- CRITICAL: compare pvalue to the row's own `alpha` (post-correction), NOT 0.05.
-- Main and secondary/guardrail metrics carry different effective alphas.
-- ============================================================================
SELECT
    experiment, metric,
    if(is_main_metric, 'main', if(is_guardrail, 'guardrail', 'secondary')) AS tier,
    name_1 AS control, name_2 AS treatment,
    end_ts AS t,
    pvalue,
    alpha  AS alpha_effective,
    (pvalue IS NOT NULL AND pvalue < alpha) AS significant_at_effective_alpha,
    reject AS abkit_reject   -- abkit's own decision (composed rule) for cross-check
FROM abkit_internal._ab_results FINAL
WHERE experiment = {experiment:String}
  AND is_horizon                       -- decision-ready rows only
ORDER BY tier, metric, control, treatment;


-- ============================================================================
-- RECIPE 5 — Power / MDE / sample sizes (planning read-back)
-- ----------------------------------------------------------------------------
-- The achieved MDE per arm and current sizes at the latest cutoff — "how much
-- could this experiment detect, and is it big enough?".
-- ============================================================================
WITH latest AS (
    SELECT experiment, metric, method_config_id, name_1, name_2,
           argMax(mde_1, end_ts) AS mde_1,
           argMax(mde_2, end_ts) AS mde_2,
           argMax(size_1, end_ts) AS size_1,
           argMax(size_2, end_ts) AS size_2,
           argMax(effect, end_ts) AS effect,
           max(end_ts) AS as_of
    FROM abkit_internal._ab_results FINAL
    GROUP BY experiment, metric, method_config_id, name_1, name_2
)
SELECT experiment, metric, name_1 AS control, name_2 AS treatment,
       size_1, size_2,
       round(mde_2, 4) AS achieved_mde,       -- treatment-arm MDE
       round(effect, 4) AS observed_effect,
       (abs(effect) >= mde_2) AS effect_exceeds_mde,
       as_of
FROM latest
WHERE experiment = {experiment:String}
ORDER BY metric, control, treatment;


-- ============================================================================
-- RECIPE 6 — Cross-experiment scoreboard (portfolio view)
-- ----------------------------------------------------------------------------
-- Latest decision-ready verdict for every experiment's MAIN metric — the
-- "what's winning right now" board across the whole program.
-- ============================================================================
WITH latest AS (
    SELECT experiment, metric, method_config_id, name_1, name_2,
           argMax(effect, end_ts) AS effect,
           argMax(reject, end_ts) AS reject,
           argMax(alpha, end_ts) AS alpha,
           argMax(pvalue, end_ts) AS pvalue,
           argMax(srm_flag, end_ts) AS srm_flag,
           argMax(decision_blocked, end_ts) AS decision_blocked,
           argMax(insufficient_data, end_ts) AS insufficient_data,
           argMax(is_horizon, end_ts) AS is_horizon,
           argMax(ci_kind, end_ts) AS ci_kind,
           argMax(is_main_metric, end_ts) AS is_main_metric,
           max(end_ts) AS as_of
    FROM abkit_internal._ab_results FINAL
    GROUP BY experiment, metric, method_config_id, name_1, name_2
)
SELECT experiment, metric, name_1 AS control, name_2 AS treatment,
       round(effect, 4) AS effect, as_of,
       multiIf(
           decision_blocked OR srm_flag, 'BLOCKED (SRM)',
           insufficient_data,           'INSUFFICIENT',
           NOT (is_horizon OR ci_kind = 'always_valid'), 'PENDING',
           reject AND effect > 0,       'WIN',
           reject AND effect < 0,       'LOSE',
           NOT reject,                  'FLAT',
           'INCONCLUSIVE'
       ) AS verdict
FROM latest
WHERE is_main_metric
ORDER BY experiment, control, treatment;


-- ============================================================================
-- RECIPE 7 — Recent activity / freshness (is the pipeline running?)
-- ----------------------------------------------------------------------------
-- The newest cutoff and when it was computed per experiment — a staleness monitor
-- for your scheduled `abk run`.
-- ============================================================================
SELECT experiment,
       max(end_ts)      AS latest_cutoff,
       max(created_at)  AS last_computed_at,
       count(DISTINCT metric)           AS metrics,
       count(DISTINCT method_config_id) AS method_configs
FROM abkit_internal._ab_results FINAL
GROUP BY experiment
ORDER BY last_computed_at DESC;


-- ============================================================================
-- RECIPE 8 — Config-drift / orphan detector (>1 method_config_id per series)
-- ----------------------------------------------------------------------------
-- More than one method_config_id for a (experiment, metric) means an identity
-- param was edited and the old result series is orphaned — charts will show
-- duplicate stabilization lines. Run `abk clean --select <experiment>` to prune.
-- ============================================================================
SELECT experiment, metric,
       count(DISTINCT method_config_id) AS n_method_configs,
       groupArray(DISTINCT method_config_id) AS method_config_ids
FROM abkit_internal._ab_results FINAL
GROUP BY experiment, metric
HAVING n_method_configs > 1
ORDER BY n_method_configs DESC, experiment, metric;
