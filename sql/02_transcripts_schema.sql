-- Transcript tables, loaded from AIxBlock CallCenterEN (CC BY-NC 4.0, research only; raw data never committed).
--
-- calls      : one row per unique call (deduplicated by SHA-256 of the raw JSON bytes).
-- call_words : one row per transcribed word, with timing (ms from call start) and ASR confidence.
--
-- Caveats baked into the source (see README):
--   * speaker is NULL in every file sampled -> no agent/customer split.
--   * no accent field in the JSON -> only domain/direction (from the zip name) are available as segments.
--   * most domains contain no calls shorter than ~3 min -> short/abandoned calls look filtered out upstream.

DROP TABLE IF EXISTS call_words;
DROP TABLE IF EXISTS calls;

CREATE TABLE calls (
    call_id            INTEGER PRIMARY KEY,
    source_zip         TEXT     NOT NULL,
    source_file        TEXT     NOT NULL,
    content_sha256     TEXT     NOT NULL UNIQUE,  -- dedup key
    domain             TEXT     NOT NULL,         -- from the zip name
    direction          TEXT     NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    audio_duration_s   INTEGER,                   -- as reported in the file
    asr_confidence     REAL,                      -- file-level confidence from the ASR model
    n_words            INTEGER  NOT NULL,
    n_words_rejected   INTEGER  NOT NULL,         -- words dropped by the loader (missing/invalid timing)
    first_word_ms      INTEGER,
    last_word_ms       INTEGER,
    transcript         TEXT     NOT NULL
);

CREATE TABLE call_words (
    call_id     INTEGER  NOT NULL,
    word_idx    INTEGER  NOT NULL,                -- 0-based position in the call
    word        TEXT     NOT NULL,
    start_ms    INTEGER  NOT NULL,
    end_ms      INTEGER  NOT NULL,
    confidence  REAL,
    speaker     TEXT                              -- kept for completeness; NULL in practice
);
-- Primary key, foreign key and indexes are added AFTER the bulk load (see load_transcripts.py):
-- building them once at the end is much faster than maintaining them row by row.
