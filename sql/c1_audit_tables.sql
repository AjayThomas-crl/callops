-- C1. Outcome audit tables.
--
-- audit_sample   : 50 calls per domain, chosen reproducibly. ORDER BY md5(seed || call_id) is a
--                  deterministic shuffle: same sample on every machine and every rerun, unlike random().
--                  sample_rank interleaves domains, so labelling in rank order keeps the sample balanced
--                  even if labelling stops early (e.g. after 25 per domain).
-- outcome_labels : one row per (call, labeller). labeller = 'human' or 'llm:<model>'.
--
-- NEVER DROP outcome_labels: human labels take hours to produce. Hence CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS audit_sample AS
SELECT call_id, domain, sample_rank
FROM (
    SELECT call_id, domain,
           row_number() OVER (PARTITION BY domain ORDER BY md5('callops-audit-v1' || call_id)) AS sample_rank
    FROM calls
) t
WHERE sample_rank <= 50;

CREATE TABLE IF NOT EXISTS outcome_labels (
    call_id         INTEGER     NOT NULL REFERENCES calls (call_id),
    labeler         TEXT        NOT NULL,
    prompt_version  TEXT        NOT NULL,
    outcome         TEXT        NOT NULL,
    confidence      REAL,
    evidence        TEXT,
    notes           TEXT,
    raw_response    TEXT,        -- full LLM reply, kept for debugging parse failures
    answered_by     TEXT,        -- model that actually replied (routers can hand off to another model)
    labeled_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (call_id, labeler)
);
