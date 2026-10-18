-- A2. When to stop redialling: conversion rate OF THE Nth CALL, and what a cap at N would cost.
--
-- Why not just "conversion rate of clients with campaign_contacts = N"?
--   That group is "people we stopped at N". To decide whether to MAKE call N we need
--   "of everyone who was still being called at attempt N, how many converted on that call?"
--   = conversions_at_n / clients_reaching_n            (a hazard rate, as in survival analysis)
--   clients_reaching_n = clients with campaign_contacts >= N  -> a reverse running total.
--
-- ASSUMPTION (state it out loud): calling stops once a client converts, so a converted
-- client with campaign_contacts = N converted on call N. The dataset does not say this explicitly.
--
-- Caveats:
--   * Who gets redialled is not random (agents chase warm leads), so a high hazard at N is not
--     proof that call N causes conversions. A capped A/B test would settle it.
--   * 2008 dominates (67% of clients) and called much more aggressively (max 56 calls vs 16 in 2010).
--   * The window functions run over ALL attempts; the final WHERE only trims the display.
--     Filtering before the window would silently change the running totals.

WITH per_n AS (
    SELECT
        campaign_contacts                 AS n,
        count(*)                          AS stopped_at_n,
        count(*) FILTER (WHERE converted) AS conversions_at_n
    FROM bank_contacts
    GROUP BY campaign_contacts
),
windowed AS (
    SELECT
        n,
        stopped_at_n,
        conversions_at_n,
        -- everyone who stopped at n or later was called at least n times
        sum(stopped_at_n)     OVER (ORDER BY n DESC) AS reaching_n,
        sum(conversions_at_n) OVER (ORDER BY n)      AS cum_conversions,
        sum(conversions_at_n) OVER ()                AS total_conversions
    FROM per_n
),
costed AS (
    SELECT
        *,
        -- each client reaching attempt n costs one call at attempt n
        sum(reaching_n) OVER (ORDER BY n) AS cum_calls,
        sum(reaching_n) OVER ()           AS total_calls
    FROM windowed
)
SELECT
    n                                                         AS attempt,
    reaching_n                                                AS clients_called_at_n,
    conversions_at_n,
    round(100.0 * conversions_at_n / reaching_n, 2)           AS conv_rate_of_call_n_pct,
    round(reaching_n::numeric / nullif(conversions_at_n, 0), 0) AS calls_per_conversion_at_n,
    -- "if we capped at n calls": what we keep vs what we spend
    round(100.0 * cum_conversions / total_conversions, 1)     AS pct_conversions_kept_if_cap,
    round(100.0 * cum_calls / total_calls, 1)                 AS pct_calls_spent_if_cap
FROM costed
WHERE n <= 15
ORDER BY n;
