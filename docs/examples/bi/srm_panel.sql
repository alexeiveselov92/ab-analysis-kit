-- ab-analysis-kit — optional SRM (sample-ratio-mismatch) monitoring panel
-- ============================================================================
-- SRM = the observed arm split drifting from the assigned split. It means the
-- randomization or the cohort query is broken, and abkit BLOCKS the decision when
-- it fires (`decision_blocked = true`). A significant effect on top of an SRM-failed
-- experiment is NOT trustworthy — this panel is the "is the experiment even valid?"
-- guard that belongs above every effect chart.
--
-- ClickHouse form (read FINAL). PG/MySQL: drop `FINAL`.
-- `srm_flag`   — abkit's gate tripped (χ² at daily+ cadence; anytime-valid multinomial
--                below 1d). `srm_pvalue` — the gate p-value (default threshold 0.001).
-- Note: the *expected* split lives in the experiment YAML, not in `_ab_results`; this
-- panel reports the OBSERVED split + abkit's verdict. A healthy 50/50 sits at ~0.5.


-- ----------------------------------------------------------------------------
-- PANEL A — SRM status per experiment (latest cutoff): the red/green board
-- ----------------------------------------------------------------------------
WITH latest AS (
    SELECT experiment, metric, method_config_id, name_1, name_2,
           argMax(size_1, end_ts)     AS size_1,
           argMax(size_2, end_ts)     AS size_2,
           argMax(srm_flag, end_ts)   AS srm_flag,
           argMax(srm_pvalue, end_ts) AS srm_pvalue,
           max(end_ts)                AS as_of
    FROM abkit_internal._ab_results FINAL
    GROUP BY experiment, metric, method_config_id, name_1, name_2
)
SELECT experiment, metric, name_1 AS control, name_2 AS treatment,
       size_1, size_2,
       round(size_2 / nullIf(size_1 + size_2, 0), 4) AS observed_treatment_share,
       srm_pvalue,
       if(srm_flag, 'SRM FAIL — do not trust effects', 'ok') AS srm_status,
       as_of
FROM latest
GROUP BY experiment, metric, control, treatment, size_1, size_2, srm_pvalue, srm_flag, as_of
ORDER BY srm_flag DESC, experiment, metric;


-- ----------------------------------------------------------------------------
-- PANEL B — observed arm share over time (drift detector, single experiment)
-- ----------------------------------------------------------------------------
-- Plot `observed_treatment_share` vs `t`; a healthy balanced split hugs its target
-- (e.g. 0.5). A drift away from target that trips `srm_flag` is the alarm.
SELECT
    end_ts AS t,
    name_1 AS control, name_2 AS treatment,
    size_1, size_2,
    size_2 / nullIf(size_1 + size_2, 0) AS observed_treatment_share,
    srm_pvalue,
    srm_flag
FROM abkit_internal._ab_results FINAL
WHERE experiment = {experiment:String}
  AND metric     = {metric:String}
ORDER BY method_config_id, name_1, name_2, end_ts;


-- ----------------------------------------------------------------------------
-- PANEL C — count of currently SRM-blocked comparisons (single stat / alert)
-- ----------------------------------------------------------------------------
-- Point a Grafana stat panel + threshold alert at this: > 0 means at least one live
-- experiment has a broken split right now.
WITH latest AS (
    SELECT experiment, metric, name_1, name_2,
           argMax(srm_flag, end_ts) AS srm_flag
    FROM abkit_internal._ab_results FINAL
    GROUP BY experiment, metric, name_1, name_2
)
SELECT countIf(srm_flag) AS srm_failing_comparisons
FROM latest;
