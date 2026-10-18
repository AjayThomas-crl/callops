-- B3. ASR failure stretches: runs of consecutive low-confidence words ("gaps and islands").
--
-- One low-confidence word is noise. Three or more in a row means the ASR lost the thread
-- (crosstalk, noise, accent, jargon), which is what makes a voice bot answer the wrong thing.
--
-- low word  = confidence in the bottom 1% OF ITS OWN ASR PIPELINE, and not a redaction token
--             like [PERSON_NAME] (those are not real words). Pipeline-relative because the two
--             pipelines use different scales (p1: A = 0.52, B = 0.25), see caveat below.
-- island    = maximal run of consecutive low words in a call, found with:
--             word_idx - row_number() over (low words of the call, in order)
--             -> constant within a run, changes after every break. GROUP BY it to get one row per run.
-- failure stretch = island of >= 3 words.
--
-- ASR PIPELINE CAVEAT: auto_health_insurance confidences come from a different export
-- (5-decimal values vs 7-8 elsewhere, checked on 60 raw files per zip) and run systematically lower,
-- while humans in those calls ask each other to repeat LESS often. Confidence is therefore compared
-- WITHIN a pipeline only, and "low" is defined per pipeline.

DROP TABLE IF EXISTS asr_low_runs;

CREATE TABLE asr_low_runs AS
WITH words AS (
    SELECT w.*,
           CASE WHEN c.domain = 'auto_health_insurance' THEN 'B' ELSE 'A' END AS asr_pipeline
    FROM call_words w JOIN calls c USING (call_id)
    WHERE w.word NOT LIKE '[%]%'
),
thresholds AS (
    SELECT asr_pipeline, percentile_disc(0.01) WITHIN GROUP (ORDER BY confidence) AS p1
    FROM words GROUP BY asr_pipeline
),
low AS (
    SELECT
        call_id, word_idx, word, start_ms, end_ms, confidence,
        word_idx - row_number() OVER (PARTITION BY call_id ORDER BY word_idx) AS island
    FROM words JOIN thresholds USING (asr_pipeline)
    WHERE confidence < p1
)
SELECT
    call_id,
    min(word_idx)                                   AS first_word_idx,
    max(word_idx)                                   AS last_word_idx,
    count(*)                                        AS n_words,
    min(start_ms)                                   AS start_ms,
    max(end_ms)                                     AS end_ms,
    round(avg(confidence)::numeric, 3)              AS avg_conf,
    string_agg(word, ' ' ORDER BY word_idx)         AS heard_as
FROM low
GROUP BY call_id, island
HAVING count(*) >= 3;

ALTER TABLE asr_low_runs ADD PRIMARY KEY (call_id, first_word_idx);
ANALYZE asr_low_runs;

-- Impact per domain, with the pipeline made explicit. LEFT JOIN keeps calls with no failures.
WITH per_call AS (
    SELECT call_id, count(*) AS runs, sum(n_words) AS words_in_runs, max(n_words) AS longest_run
    FROM asr_low_runs GROUP BY call_id
)
SELECT
    CASE WHEN c.domain = 'auto_health_insurance' THEN 'B' ELSE 'A' END AS asr_pipeline,
    c.domain,
    count(*)                                                        AS calls,
    count(p.call_id)                                                AS calls_with_failure,
    round(100.0 * count(p.call_id) / count(*), 1)                   AS pct_calls_with_failure,
    round(100.0 * count(*) FILTER (WHERE p.longest_run >= 6) / count(*), 1) AS pct_calls_run_6plus,
    round(coalesce(sum(p.runs), 0)::numeric / count(*), 2)          AS runs_per_call,
    round(100.0 * coalesce(sum(p.words_in_runs), 0) / sum(c.n_words), 2) AS pct_words_in_runs
FROM calls c
LEFT JOIN per_call p USING (call_id)
GROUP BY 1, 2
ORDER BY 1, pct_calls_with_failure DESC;
