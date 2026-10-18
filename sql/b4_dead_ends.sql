-- B4. Dead-end calls: how did each call end, and at which conversation step do no-closure endings cluster?
--
-- ending_type, from the last 25 words (rules checked in order, first match wins):
--   transfer     : handed to another agent/line ("connect you", "kicked from this conference",
--                  "over to you", pre-transfer disclaimers like "for quality and training purposes")
--   refused      : the customer ended it ("not interested", "hang up", "stop calling")
--                  checked BEFORE polite_close because refusals often still say "goodbye"
--   polite_close : bye / thank you / have a good day / take care
--   no_closure   : none of the above -> the recording stops without a goodbye. NOT the same as the
--                  customer dropping: in insurance outbound (Medicare lead-gen: qualify, then hand to a
--                  licensed agent) samples show many recordings end at the handoff itself.
--
-- last_step: the conversation step mentioned LATEST in the final 40 words, found by sliding a
-- 3-word window over them (multi-word cues like "zip code" need it) and keeping the step with the
-- highest word position.
--
-- Step no-closure rate (report 2) = no_closure endings at step X / calls that mention step X anywhere.
-- Same idea as the redial hazard in a2: a step every call passes through will top a raw count
-- of endings without being any more dangerous.
--
-- Caveats:
--   * Keyword rules, unvalidated: precision needs a hand-labelled sample.
--   * no_closure may be the RECORDING stopping (e.g. at a transfer), not the customer leaving, and a
--     step with a high no-closure rate may be the SUCCESS point (qualified -> transferred). Telling them
--     apart needs the call disposition from the dialler/CRM, which this dataset does not have.
--   * ends_mid_sentence is weak: the ASR adds a full stop even to cut-off fragments.
--   * No speaker labels: ends_on_question means the last utterance was a question, which usually is the
--     agent asking and getting no answer, but we cannot confirm who spoke.

DROP TABLE IF EXISTS call_endings;

CREATE TABLE call_endings AS
WITH steps (step, priority, pattern) AS (
    VALUES
        ('payment_banking',   1, '(card number|credit card|debit card|bank account|routing|account number|social security|\mssn\M|payment|\[CREDIT_CARD|\[BANKING|\[US_SOCIAL)'),
        ('identity_check',    2, '(date of birth|birthday|verify your|your age|\[DATE_OF_BIRTH\]|\[PERSON_AGE\]|first and last name|spell)'),
        ('medicare_eligibility', 3, '(medicare|medicaid|part a|part b|red, white|\mmbi\M)'),
        ('health_screening',  4, '(doctor|checkup|prescription|medication|\[MEDICAL_CONDITION\]|\[DRUG\]|hospital)'),
        ('vehicle_details',   5, '(vehicle|make and model|\mvin\M|driver|\[VEHICLE_ID\])'),
        ('current_coverage',  6, '(current (insurance|coverage|carrier|policy)|premium|paying|deductible|coverage)'),
        ('contact_details',   7, '(zip code|address|phone number|email|\[LOCATION\]|\[PHONE_NUMBER\]|\[EMAIL_ADDRESS\])'),
        ('greeting',          8, '(how are you|good (morning|afternoon|evening)|my name is|speaking with|calling (from|on behalf))')
),
tail AS (
    SELECT w.call_id, w.word_idx,
           string_agg(w.word, ' ') OVER (PARTITION BY w.call_id ORDER BY w.word_idx
                                         ROWS BETWEEN CURRENT ROW AND 2 FOLLOWING) AS window3
    FROM call_words w JOIN calls c USING (call_id)
    WHERE w.word_idx >= c.n_words - 40
),
step_hits AS (
    SELECT DISTINCT ON (t.call_id) t.call_id, s.step
    FROM tail t JOIN steps s ON t.window3 ~* s.pattern
    ORDER BY t.call_id, t.word_idx DESC, s.priority          -- latest position wins, priority breaks ties
),
endings AS (
    SELECT w.call_id,
           string_agg(w.word, ' ' ORDER BY w.word_idx) AS ending_text,
           (array_agg(w.word ORDER BY w.word_idx DESC))[1] AS last_word
    FROM call_words w JOIN calls c USING (call_id)
    WHERE w.word_idx >= c.n_words - 25
    GROUP BY w.call_id
)
SELECT
    e.call_id,
    CASE
        WHEN e.ending_text ~* '(connect(ing)? you|transferr|(kicked|picked) from (this|the) conference|licensed (agent|specialist)|stay on the line|over to you|quality and training|thank you for holding|hold the line)'
            THEN 'transfer'
        WHEN e.ending_text ~* '(not interested|hang up|hung up|stop calling|don.t call|do not call|remove (me|my)|leave me alone|no,? thank you)'
            THEN 'refused'
        WHEN e.ending_text ~* '(\mbye\M|goodbye|thank you|thanks|have a (good|great|nice|wonderful|blessed)|take care|see you|talk to you)'
            THEN 'polite_close'
        ELSE 'no_closure'
    END                                  AS ending_type,
    coalesce(h.step, 'unknown')          AS last_step,
    e.last_word !~ '[.?!]$'              AS ends_mid_sentence,
    e.last_word ~ '\?$'                  AS ends_on_question,
    e.ending_text
FROM endings e
LEFT JOIN step_hits h USING (call_id);

ALTER TABLE call_endings ADD PRIMARY KEY (call_id);
ANALYZE call_endings;

-- Report 1: how calls end, per domain
SELECT
    c.domain,
    count(*)                                                                  AS calls,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'polite_close') / count(*), 1) AS polite_pct,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'transfer')     / count(*), 1) AS transfer_pct,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'refused')      / count(*), 1) AS refused_pct,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'no_closure')   / count(*), 1) AS no_closure_pct,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'no_closure' AND ends_mid_sentence) / count(*), 1) AS no_closure_mid_sentence_pct,
    round(100.0 * count(*) FILTER (WHERE ending_type = 'no_closure' AND ends_on_question)  / count(*), 1) AS no_closure_on_question_pct
FROM call_endings JOIN calls c USING (call_id)
GROUP BY ROLLUP (c.domain)
ORDER BY c.domain IS NULL, no_closure_pct DESC;

-- Report 2: step no-closure rate = no_closure endings at the step / calls that reached the step
WITH steps (step, pattern) AS (
    VALUES
        ('payment_banking',   '(card number|credit card|debit card|bank account|routing|account number|social security|\mssn\M|payment|\[CREDIT_CARD|\[BANKING|\[US_SOCIAL)'),
        ('identity_check',    '(date of birth|birthday|verify your|your age|\[DATE_OF_BIRTH\]|\[PERSON_AGE\]|first and last name|spell)'),
        ('medicare_eligibility', '(medicare|medicaid|part a|part b|red, white|\mmbi\M)'),
        ('health_screening',  '(doctor|checkup|prescription|medication|\[MEDICAL_CONDITION\]|\[DRUG\]|hospital)'),
        ('vehicle_details',   '(vehicle|make and model|\mvin\M|driver|\[VEHICLE_ID\])'),
        ('current_coverage',  '(current (insurance|coverage|carrier|policy)|premium|paying|deductible|coverage)'),
        ('contact_details',   '(zip code|address|phone number|email|\[LOCATION\]|\[PHONE_NUMBER\]|\[EMAIL_ADDRESS\])'),
        ('greeting',          '(how are you|good (morning|afternoon|evening)|my name is|speaking with|calling (from|on behalf))')
),
reached AS (
    SELECT c.domain, s.step, count(*) AS calls_reaching
    FROM calls c JOIN steps s ON c.transcript ~* s.pattern
    GROUP BY 1, 2
),
died AS (
    SELECT c.domain, e.last_step AS step, count(*) AS no_closure_endings
    FROM call_endings e JOIN calls c USING (call_id)
    WHERE e.ending_type = 'no_closure'
    GROUP BY 1, 2
)
SELECT
    r.domain,
    r.step,
    r.calls_reaching,
    coalesce(d.no_closure_endings, 0)                                 AS no_closure_endings_here,
    round(100.0 * coalesce(d.no_closure_endings, 0) / r.calls_reaching, 1) AS step_no_closure_pct
FROM reached r
LEFT JOIN died d USING (domain, step)
WHERE r.calls_reaching >= 50                                          -- too few calls to compare otherwise
ORDER BY r.domain, step_no_closure_pct DESC;
