-- B2. Silence impact, sized in calls per domain.
--
-- Severity tiers (built on call_silences from b1):
--   noticed_dead_air : unannounced silence after which someone asked "are you there / can you hear me"
--   long_unannounced : unannounced silence >= 30 s (too long to be just typing an answer)
--   silent_share     : % of the call's audio spent in gaps >= 5 s, any category
--
-- JOIN TRAP: calls LEFT JOIN per-call silence stats. An INNER JOIN would drop every call with
-- no silences, shrinking the denominator and inflating every "% of calls" figure.
--
-- Caveats: keyword-based categories (see b1); no speaker labels, so we cannot say whether the
-- agent or the customer went silent; source appears to exclude calls under ~3 min.

WITH per_call AS (
    SELECT
        call_id,
        bool_or(category = 'unannounced' AND other_side_checked_in)  AS has_noticed_dead_air,
        bool_or(category = 'unannounced' AND gap_ms >= 30000)        AS has_long_unannounced,
        sum(gap_ms)                                                  AS silent_ms,
        sum(gap_ms) FILTER (WHERE category = 'unannounced')          AS unannounced_ms,
        sum(gap_ms) FILTER (WHERE category = 'announced_hold')       AS hold_ms
    FROM call_silences
    GROUP BY call_id
),
joined AS (
    SELECT
        c.domain,
        c.direction,
        c.audio_duration_s,
        coalesce(p.has_noticed_dead_air, false) AS has_noticed_dead_air,
        coalesce(p.has_long_unannounced, false) AS has_long_unannounced,
        coalesce(p.silent_ms, 0)                AS silent_ms,
        coalesce(p.unannounced_ms, 0)           AS unannounced_ms,
        coalesce(p.hold_ms, 0)                  AS hold_ms
    FROM calls c
    LEFT JOIN per_call p USING (call_id)
),
by_domain AS (
    SELECT domain, direction, count(*) AS calls,
           count(*) FILTER (WHERE has_noticed_dead_air) AS noticed_dead_air,
           count(*) FILTER (WHERE has_long_unannounced) AS long_unannounced,
           count(*) FILTER (WHERE has_noticed_dead_air OR has_long_unannounced) AS any_severe,
           sum(silent_ms) AS silent_ms, sum(unannounced_ms) AS unannounced_ms, sum(hold_ms) AS hold_ms,
           sum(audio_duration_s) * 1000 AS audio_ms
    FROM joined
    GROUP BY GROUPING SETS ((domain, direction), ())
)
SELECT
    coalesce(domain, 'ALL')                                       AS domain,
    direction,
    calls,
    noticed_dead_air,
    round(100.0 * noticed_dead_air / calls, 1)                    AS noticed_pct,
    long_unannounced,
    round(100.0 * long_unannounced / calls, 1)                    AS long_unannounced_pct,
    round(100.0 * any_severe / calls, 1)                          AS any_severe_pct,
    round(100.0 * silent_ms / audio_ms, 1)                        AS silent_share_pct,
    round(unannounced_ms / 60000.0 / calls, 2)                    AS unannounced_min_per_call,
    round(hold_ms / 60000.0 / calls, 2)                           AS hold_min_per_call
FROM by_domain
ORDER BY domain IS NULL, any_severe_pct DESC;
