-- A4. Does the previous campaign's outcome predict this one?
--
-- prev_outcome:  nonexistent = never in a previous campaign
--                failure / success = outcome of the previous campaign
-- lift = segment conversion rate / overall conversion rate.
--
-- Caveats:
--   * Correlation, not causation: past converters are just better prospects, calling them
--     again does not "cause" the conversion.
--   * Small segments get a margin of error so a big lift on few clients is not over-read.
--   * Mixed across years; 2010 had far more previously contacted clients (see the year split).

WITH overall AS (
    SELECT avg(converted::int)::numeric AS p_all FROM bank_contacts
),
seg AS (
    SELECT
        prev_outcome,
        count(*)                          AS clients,
        avg(converted::int)::numeric      AS p,
        round(100.0 * count(*) FILTER (WHERE contact_year = 2010) / count(*), 0) AS pct_from_2010
    FROM bank_contacts
    GROUP BY prev_outcome
)
SELECT
    prev_outcome,
    clients,
    round(100 * p, 1)                                  AS conv_pct,
    round(100 * 1.96 * sqrt(p * (1 - p) / clients), 1) AS margin_95_pct,
    round(p / p_all, 2)                                AS lift_vs_overall,
    pct_from_2010
FROM seg CROSS JOIN overall
ORDER BY conv_pct DESC;
