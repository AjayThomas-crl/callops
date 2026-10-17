-- bank_contacts: one row per CLIENT contacted in a campaign (not one row per call).
-- Source: UCI Bank Marketing, bank-additional-full.csv (41,188 rows, May 2008 - Nov 2010).
--
-- Cleaning decisions (made in load_bank.py, documented here):
--   * row_id       = position in the source file. The file is ordered by date, so row_id is chronological.
--   * contact_year = INFERRED from where the month sequence wraps (dec -> mar happens exactly twice).
--   * pdays        = 999 means "never contacted before" -> stored as NULL, flag in was_previously_contacted.
--   * y            = 'yes'/'no' -> boolean converted.
--   * 'unknown'    kept as-is in categorical columns (the dataset's own missing-value label).
--   * `default` is a reserved word in SQL -> renamed has_credit_default.

DROP TABLE IF EXISTS bank_contacts;

CREATE TABLE bank_contacts (
    row_id                   INTEGER PRIMARY KEY,
    -- client
    age                      SMALLINT NOT NULL,
    job                      TEXT     NOT NULL,
    marital                  TEXT     NOT NULL,
    education                TEXT     NOT NULL,
    has_credit_default       TEXT     NOT NULL,   -- yes / no / unknown
    has_housing_loan         TEXT     NOT NULL,
    has_personal_loan        TEXT     NOT NULL,
    -- last contact of this campaign
    contact_channel          TEXT     NOT NULL,   -- cellular / telephone
    contact_year             SMALLINT NOT NULL,   -- inferred, see header
    contact_month            SMALLINT NOT NULL CHECK (contact_month BETWEEN 1 AND 12),
    day_of_week              TEXT     NOT NULL,   -- mon..fri
    last_call_duration_s     INTEGER  NOT NULL,   -- LEAKS the outcome: known only after the call
    -- contact history
    campaign_contacts        SMALLINT NOT NULL,   -- calls to this client in this campaign, incl. the last one
    days_since_prev_campaign SMALLINT,            -- NULL = never contacted before (raw value 999)
    was_previously_contacted BOOLEAN  NOT NULL,
    previous_contacts        SMALLINT NOT NULL,   -- calls in earlier campaigns
    prev_outcome             TEXT     NOT NULL,   -- failure / nonexistent / success
    -- macro context (national indicators, same for everyone in a period)
    emp_var_rate             NUMERIC(4,1),
    cons_price_idx           NUMERIC(6,3),
    cons_conf_idx            NUMERIC(4,1),
    euribor3m                NUMERIC(5,3),
    nr_employed              NUMERIC(6,1),
    -- outcome
    converted                BOOLEAN  NOT NULL    -- subscribed a term deposit
);

CREATE INDEX ON bank_contacts (contact_year, contact_month);
CREATE INDEX ON bank_contacts (campaign_contacts);
