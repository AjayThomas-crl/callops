-- C2. Outcome audit results: does the "recorded outcome" (LLM) match ground truth (human)?
--
-- Only calls labelled by BOTH sides of a pair are compared (inner join on call_id), so a partial
-- human labelling run is still a valid, smaller sample. n is shown everywhere for that reason.
--
-- agreement     = share of calls where both gave the same label
-- cohen_kappa   = (p_observed - p_chance) / (1 - p_chance)
--                 p_chance = sum over labels of P(A says k) * P(B says k): how often two labellers
--                 would match by luck given how often each uses each label. 0 = chance, 1 = perfect.
-- margin_95     = normal-approximation margin on agreement; with n = 200 and ~80% agreement, about +-6 pts.
--
-- Caveats: one human labeller (no inter-human agreement measured); the human saw the last 300 words
-- first, the LLM always saw the full transcript; LLM labels are a stand-in for a production system's
-- recorded outcome, not the real thing.

-- 1. Coverage, and which model actually answered (a router like typesafe/jev-router hands calls to
--    other models; NULL = recorded before answered_by existed)
SELECT labeler, prompt_version, answered_by, count(*) AS labelled
FROM outcome_labels GROUP BY 1, 2, 3 ORDER BY 1, 3;

-- 2. Pairwise agreement + kappa for every pair of labellers
WITH pairs AS (
    SELECT a.labeler AS labeler_a, b.labeler AS labeler_b, a.call_id, a.outcome AS a_out, b.outcome AS b_out
    FROM outcome_labels a
    JOIN outcome_labels b ON a.call_id = b.call_id AND a.labeler < b.labeler
),
observed AS (
    SELECT labeler_a, labeler_b, count(*) AS n, avg((a_out = b_out)::int)::numeric AS p_o
    FROM pairs GROUP BY 1, 2
),
marginals AS (   -- how often each side used each label, within the pair
    SELECT labeler_a, labeler_b, k,
           avg((a_out = k)::int)::numeric AS pa,
           avg((b_out = k)::int)::numeric AS pb
    FROM pairs CROSS JOIN (SELECT DISTINCT outcome AS k FROM outcome_labels) labels
    GROUP BY 1, 2, 3
),
chance AS (
    SELECT labeler_a, labeler_b, sum(pa * pb) AS p_e FROM marginals GROUP BY 1, 2
)
SELECT
    o.labeler_a, o.labeler_b, o.n,
    round(100 * o.p_o, 1)                                   AS agreement_pct,
    round(100 * 1.96 * sqrt(o.p_o * (1 - o.p_o) / o.n), 1)  AS margin_95_pct,
    round(100 * c.p_e, 1)                                   AS chance_agreement_pct,
    round((o.p_o - c.p_e) / nullif(1 - c.p_e, 0), 3)        AS cohen_kappa
FROM observed o JOIN chance c USING (labeler_a, labeler_b)
ORDER BY o.labeler_a, o.labeler_b;

-- 3. Confusion matrix: rows = human (truth), columns = each LLM
SELECT
    l.labeler                                                AS llm,
    h.outcome                                                AS human_says,
    count(*)                                                 AS n,
    count(*) FILTER (WHERE l.outcome = 'converted')          AS "→converted",
    count(*) FILTER (WHERE l.outcome = 'transferred')        AS "→transferred",
    count(*) FILTER (WHERE l.outcome = 'callback')           AS "→callback",
    count(*) FILTER (WHERE l.outcome = 'declined')           AS "→declined",
    count(*) FILTER (WHERE l.outcome = 'resolved')           AS "→resolved",
    count(*) FILTER (WHERE l.outcome = 'unresolved')         AS "→unresolved",
    count(*) FILTER (WHERE l.outcome = 'incomplete')         AS "→incomplete"
FROM outcome_labels h
JOIN outcome_labels l ON l.call_id = h.call_id AND l.labeler LIKE 'llm:%'
WHERE h.labeler = 'human'
GROUP BY 1, 2
ORDER BY 1, 2;

-- 4. Per-outcome precision / recall of each LLM against the human
--    precision: when the LLM says k, how often is the human also k?   (trust in the recorded label)
--    recall   : of the calls the human calls k, how many did the LLM catch?
WITH j AS (
    SELECT l.labeler AS llm, h.outcome AS truth, l.outcome AS pred
    FROM outcome_labels h JOIN outcome_labels l ON l.call_id = h.call_id AND l.labeler LIKE 'llm:%'
    WHERE h.labeler = 'human'
)
SELECT
    llm, k AS outcome,
    count(*) FILTER (WHERE truth = k)                                         AS human_count,
    count(*) FILTER (WHERE pred = k)                                          AS llm_count,
    round(100.0 * count(*) FILTER (WHERE pred = k AND truth = k) / nullif(count(*) FILTER (WHERE pred = k), 0), 0)  AS precision_pct,
    round(100.0 * count(*) FILTER (WHERE pred = k AND truth = k) / nullif(count(*) FILTER (WHERE truth = k), 0), 0) AS recall_pct
FROM j CROSS JOIN (SELECT DISTINCT outcome AS k FROM outcome_labels) labels
GROUP BY llm, k
ORDER BY llm, human_count DESC;

-- 5. Where does disagreement concentrate? By domain, call length, and ASR quality.
--    ASR quality is ranked WITHIN pipeline (b3: confidence scales differ between pipelines).
WITH j AS (
    SELECT l.labeler AS llm, h.call_id, (h.outcome <> l.outcome) AS disagree
    FROM outcome_labels h JOIN outcome_labels l ON l.call_id = h.call_id AND l.labeler LIKE 'llm:%'
    WHERE h.labeler = 'human'
),
feat AS (
    SELECT c.call_id, c.domain,
           ntile(3) OVER (ORDER BY c.audio_duration_s)                              AS length_tercile,
           ntile(3) OVER (PARTITION BY (c.domain = 'auto_health_insurance') ORDER BY c.asr_confidence) AS asr_tercile
    FROM calls c WHERE c.call_id IN (SELECT call_id FROM audit_sample)
)
SELECT llm, 'domain' AS segment, domain AS value, count(*) AS n, round(100.0 * avg(disagree::int), 1) AS disagree_pct
FROM j JOIN feat USING (call_id) GROUP BY 1, 2, 3
UNION ALL
SELECT llm, 'length', CASE length_tercile WHEN 1 THEN '1 short' WHEN 2 THEN '2 medium' ELSE '3 long' END,
       count(*), round(100.0 * avg(disagree::int), 1)
FROM j JOIN feat USING (call_id) GROUP BY 1, 2, 3
UNION ALL
SELECT llm, 'asr_quality', CASE asr_tercile WHEN 1 THEN '1 worst' WHEN 2 THEN '2 middle' ELSE '3 best' END,
       count(*), round(100.0 * avg(disagree::int), 1)
FROM j JOIN feat USING (call_id) GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
