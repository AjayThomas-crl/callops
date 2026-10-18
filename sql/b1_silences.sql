-- B1. Build call_silences: every gap of >= 5 s between consecutive words, classified by context.
--
-- gap_ms = this word's start_ms - previous word's end_ms   (LAG over the call, in word order)
-- Threshold: 5 s. In this data p99 of all gaps is 3.4 s, so >= 5 s is the top ~0.6% (45,949 gaps).
--
-- Without audio we cannot hear hold music, so each gap is classified from the words around it
-- (15 words before, 8 after). Rules are checked in order, first match wins:
--   after_goodbye : someone said bye just before (post-call survey, line left open)
--   transfer_ivr  : connecting/transferring, queue or IVR language
--   announced_hold: the agent asked the customer to wait ("one moment", "let me check", "hold on")
--   unannounced   : none of the above. Mostly silent data entry (agent typing answers), some real dead air;
--                   severity is graded in b2, not assumed here
-- other_side_checked_in = the first words after the gap ask "are you still there?" style questions:
--   strong evidence that the silence was noticed by the other party. Ignored in the first 30 s of a call.
--
-- Caveats:
--   * Keyword heuristic, not ground truth. Precision must be checked by hand on a sample (still to do: label ~50 rows).
--   * No speaker labels, so we cannot say WHO went silent (agent vs customer).
--   * Silence = no transcribed words. Speech the ASR missed (noise, crosstalk) also looks like silence.

DROP TABLE IF EXISTS call_silences;

CREATE TABLE call_silences AS
WITH gaps AS (
    SELECT
        call_id,
        word_idx,                                   -- the first word AFTER the silence
        LAG(end_ms) OVER w           AS silence_start_ms,
        start_ms                     AS silence_end_ms,
        start_ms - LAG(end_ms) OVER w AS gap_ms
    FROM call_words
    WINDOW w AS (PARTITION BY call_id ORDER BY word_idx)
),
long_gaps AS (
    SELECT * FROM gaps WHERE gap_ms >= 5000
),
with_context AS (
    SELECT
        g.*,
        (SELECT string_agg(word, ' ' ORDER BY word_idx) FROM call_words cw
          WHERE cw.call_id = g.call_id AND cw.word_idx BETWEEN g.word_idx - 15 AND g.word_idx - 1) AS before_text,
        (SELECT string_agg(word, ' ' ORDER BY word_idx) FROM call_words cw
          WHERE cw.call_id = g.call_id AND cw.word_idx BETWEEN g.word_idx AND g.word_idx + 7)      AS after_text
    FROM long_gaps g
)
SELECT
    c.call_id,
    c.word_idx,
    c.silence_start_ms,
    c.silence_end_ms,
    c.gap_ms,
    -- where in the call the silence starts, 0 = start, 100 = end of audio
    round(100.0 * c.silence_start_ms / nullif(k.audio_duration_s * 1000, 0), 1) AS position_pct,
    CASE
        WHEN c.before_text ~* '\mbye\M'
            THEN 'after_goodbye'
        -- "be online" = Indian-English "stay on the line"; recorded/monitored = IVR disclaimer
        WHEN c.before_text ~* '(connect|transfer|stay on the line|be on the line|be online|queue|next available|\mpress\M|recorded|monitored|with you shortly|please wait|call is important)'
          OR c.after_text  ~* '(thank you for calling|welcome to|thank you for holding|\mpress\M|queue)'
            THEN 'transfer_ivr'
        WHEN c.before_text ~* '(\mhold\M|moment|one sec|a sec|a second|one second|a minute|bear with me|let me (check|see|look|pull|get|verify|find)|take your time|give me)'
            THEN 'announced_hold'
        ELSE 'unannounced'
    END AS category,
    -- first 30 s excluded: "Hello? Hello?" there is just people answering the phone
    c.after_text ~* '(still (on the line|there|with me)|are you there|can you hear me|hello\?)'
        AND c.silence_start_ms >= 30000 AS other_side_checked_in,
    c.before_text,
    c.after_text
FROM with_context c
JOIN calls k USING (call_id);

ALTER TABLE call_silences ADD PRIMARY KEY (call_id, word_idx);
CREATE INDEX ON call_silences (category);
ANALYZE call_silences;
