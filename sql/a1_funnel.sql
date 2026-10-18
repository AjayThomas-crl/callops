-- A1. Campaign funnel by year: clients -> calls -> conversions.
--
-- Two denominators, both valid, answering different questions:
--   conv_per_client  = converted clients / clients called      "what share of the list did we win?"
--   conv_per_100_calls = converted clients / calls made * 100  "what does each dial buy us?"
-- Mixing them up is the most common funnel mistake.
--
-- Caveats:
--   * Only reached clients are in this data (last-call duration = 0 for just 4 rows), so there is
--     no dialled -> connected stage. Real dialler data would add it.
--   * campaign_contacts counts calls in THIS campaign only; previous campaigns are separate.

WITH by_year AS (
    SELECT
        contact_year,
        count(*)                      AS clients,
        sum(campaign_contacts)        AS calls,
        count(*) FILTER (WHERE converted) AS conversions
    FROM bank_contacts
    GROUP BY contact_year
),
with_total AS (
    SELECT * FROM by_year
    UNION ALL
    SELECT NULL, sum(clients), sum(calls), sum(conversions) FROM by_year   -- NULL year = all years
)
SELECT
    coalesce(contact_year::text, 'ALL')                       AS year,
    clients,
    calls,
    round(calls::numeric / clients, 2)                        AS calls_per_client,
    conversions,
    round(100.0 * conversions / clients, 1)                   AS conv_per_client_pct,
    round(100.0 * conversions / calls, 2)                     AS conv_per_100_calls,
    round(calls::numeric / nullif(conversions, 0), 1)         AS calls_per_conversion
FROM with_total
ORDER BY contact_year NULLS LAST;
