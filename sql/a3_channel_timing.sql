-- A3. Channel and timing.
--
-- Part 1: cellular vs telephone, overall AND within each year.
--   If the overall gap differs from the within-year gaps, the overall number is confounded by
--   the year mix (Simpson's paradox territory). Always check the segmented view.
-- Part 2: conversion by year-month, with n and a 95% margin of error.
--   Month alone mixes May 2008 with May 2009 (very different campaigns), so we key on both.
--   Margin of error uses the normal approximation sqrt(p(1-p)/n) * 1.96; it is unreliable for
--   tiny n, which is exactly why small months are flagged instead of ranked.

-- Part 1
SELECT
    coalesce(contact_year::text, 'ALL')                         AS year,
    contact_channel,
    count(*)                                                    AS clients,
    round(100.0 * avg(converted::int), 1)                       AS conv_pct
FROM bank_contacts
GROUP BY GROUPING SETS ((contact_year, contact_channel), (contact_channel))
ORDER BY contact_year NULLS FIRST, contact_channel;

-- Part 2
WITH ym AS (
    SELECT
        contact_year,
        contact_month,
        count(*)                          AS clients,
        avg(converted::int)::numeric      AS p,
        avg((contact_channel = 'cellular')::int)::numeric AS cellular_share
    FROM bank_contacts
    GROUP BY contact_year, contact_month
)
SELECT
    make_date(contact_year, contact_month, 1)                   AS month,
    clients,
    round(100 * p, 1)                                           AS conv_pct,
    round(100 * 1.96 * sqrt(p * (1 - p) / clients), 1)          AS margin_95_pct,
    round(100 * cellular_share, 0)                              AS cellular_pct,
    CASE WHEN clients < 100 THEN 'too small to compare' END     AS flag
FROM ym
ORDER BY month;
