"""
Data-quality checks for CallOps. Every assumption the analysis relies on, as an automated test.

Run:  python checks.py              (exit code 1 if any FAIL, so a scheduler / CI can alert on it)
      python checks.py --verbose    (also print the observed value for passing checks)

Each check is one SQL query returning ONE number, compared with an expectation:
  FAIL = the analysis would be wrong (stop and investigate before anyone reads a dashboard)
  WARN = a known quirk drifted past its tolerance (look at it, numbers may still be usable)

Why these checks: each one encodes something that was verified by hand while building the project
(see the comments in sql/). If the data is reloaded or the source changes, they catch it.
"""
import argparse
import sys

from audit_config import OUTCOMES
from db import connect

FAIL, WARN = "FAIL", "WARN"

# (name, severity, sql returning one number, test, expectation shown to humans)
CHECKS = [
    # ---------- bank_contacts ----------
    ("bank: row count matches source file", FAIL,
     "SELECT count(*) FROM bank_contacts", lambda v: v == 41188, "= 41,188"),
    ("bank: no raw 999 sentinel left in days_since_prev_campaign", FAIL,
     "SELECT count(*) FROM bank_contacts WHERE days_since_prev_campaign = 999", lambda v: v == 0, "= 0"),
    ("bank: sentinel flag consistent (NULL days <=> never contacted)", FAIL,
     """SELECT count(*) FROM bank_contacts
        WHERE (days_since_prev_campaign IS NULL) = was_previously_contacted""", lambda v: v == 0, "= 0"),
    ("bank: inferred years are 2008-2010 only", FAIL,
     "SELECT count(*) FROM bank_contacts WHERE contact_year NOT BETWEEN 2008 AND 2010", lambda v: v == 0, "= 0"),
    ("bank: year inference holds (one monthly CPI per year-month)", FAIL,
     """SELECT count(*) FROM (SELECT 1 FROM bank_contacts GROUP BY contact_year, contact_month
                             HAVING count(DISTINCT cons_price_idx) > 1) t""", lambda v: v == 0, "= 0"),
    ("bank: CPI values never repeat across year-months", FAIL,
     """SELECT count(*) FROM (SELECT 1 FROM bank_contacts GROUP BY cons_price_idx
                             HAVING count(DISTINCT (contact_year, contact_month)) > 1) t""", lambda v: v == 0, "= 0"),
    ("bank: overall conversion rate is plausible", FAIL,
     "SELECT round(100 * avg(converted::int), 2) FROM bank_contacts", lambda v: 5 <= v <= 20, "5-20 %"),
    ("bank: exact duplicate rows (no client id, so kept on purpose)", WARN,
     """SELECT count(*) - count(DISTINCT (age, job, marital, education, has_credit_default, has_housing_loan,
            has_personal_loan, contact_channel, contact_year, contact_month, day_of_week, last_call_duration_s,
            campaign_contacts, days_since_prev_campaign, previous_contacts, prev_outcome, emp_var_rate,
            cons_price_idx, cons_conf_idx, euribor3m, nr_employed, converted))
        FROM bank_contacts""", lambda v: v <= 12, "<= 12 (known)"),

    # ---------- calls / call_words ----------
    ("calls: row count matches the deduplicated subset", FAIL,
     "SELECT count(*) FROM calls", lambda v: v == 8079, "= 8,079"),
    ("calls: no duplicate content hashes", FAIL,
     "SELECT count(*) - count(DISTINCT content_sha256) FROM calls", lambda v: v == 0, "= 0"),
    ("calls: all 4 subset domains present", FAIL,
     "SELECT count(DISTINCT domain) FROM calls", lambda v: v == 4, "= 4"),
    ("calls: n_words matches call_words for every call", FAIL,
     """SELECT count(*) FROM calls c
        WHERE c.n_words <> (SELECT count(*) FROM call_words w WHERE w.call_id = c.call_id)""", lambda v: v == 0, "= 0"),
    ("words: end >= start for every word", FAIL,
     "SELECT count(*) FROM call_words WHERE end_ms < start_ms", lambda v: v == 0, "= 0"),
    ("words: confidence within [0, 1]", FAIL,
     "SELECT count(*) FROM call_words WHERE confidence < 0 OR confidence > 1", lambda v: v == 0, "= 0"),
    ("words: no word after the audio ends (+1 s tolerance)", FAIL,
     "SELECT count(*) FROM calls WHERE last_word_ms > audio_duration_s * 1000 + 1000", lambda v: v == 0, "= 0"),
    ("words: speaker labels still absent (analysis assumes none)", WARN,
     "SELECT count(*) FROM call_words WHERE speaker IS NOT NULL", lambda v: v == 0, "= 0"),
    ("words: overlapping timestamps (ASR quirk)", WARN,
     """SELECT count(*) FROM (SELECT start_ms - LAG(end_ms) OVER (PARTITION BY call_id ORDER BY word_idx) AS gap
                             FROM call_words) g WHERE gap < 0""", lambda v: v <= 100, "<= 100 (79 known)"),
    ("calls: share of calls under 3 min (source looks filtered)", WARN,
     "SELECT round(100 * avg((audio_duration_s < 180)::int), 2) FROM calls", lambda v: v <= 5, "<= 5 %"),

    # ---------- derived tables ----------
    ("silences: every row is a gap >= 5 s", FAIL,
     "SELECT count(*) FROM call_silences WHERE gap_ms < 5000", lambda v: v == 0, "= 0"),
    ("silences: categories are the documented four", FAIL,
     """SELECT count(*) FROM call_silences
        WHERE category NOT IN ('after_goodbye', 'transfer_ivr', 'announced_hold', 'unannounced')""",
     lambda v: v == 0, "= 0"),
    ("endings: one ending per call", FAIL,
     "SELECT (SELECT count(*) FROM calls) - (SELECT count(*) FROM call_endings)", lambda v: v == 0, "= 0"),

    # ---------- audit ----------
    ("audit: sample is 50 calls per domain", FAIL,
     "SELECT count(*) FROM (SELECT domain FROM audit_sample GROUP BY domain HAVING count(*) <> 50) t",
     lambda v: v == 0, "= 0 domains off"),
    ("audit: every label is one of the defined outcomes", FAIL,
     "SELECT count(*) FROM outcome_labels WHERE NOT (outcome = ANY(%(outcomes)s))", lambda v: v == 0, "= 0"),
    ("audit: labels only on sampled calls", FAIL,
     "SELECT count(*) FROM outcome_labels WHERE call_id NOT IN (SELECT call_id FROM audit_sample)",
     lambda v: v == 0, "= 0"),
    ("audit: each model labeller covered the full sample", WARN,
     """SELECT count(*) FROM (SELECT labeler FROM outcome_labels WHERE labeler LIKE 'llm:%%'
                             GROUP BY labeler HAVING count(*) <> 200) t""", lambda v: v == 0, "= 0 incomplete"),
]

# Schema drift: the columns each table must have (extra columns are fine, missing ones are not).
EXPECTED_COLUMNS = {
    "bank_contacts": {"row_id", "contact_year", "contact_month", "campaign_contacts", "days_since_prev_campaign",
                      "was_previously_contacted", "prev_outcome", "contact_channel", "converted"},
    "calls": {"call_id", "domain", "direction", "audio_duration_s", "asr_confidence", "n_words",
              "first_word_ms", "last_word_ms", "content_sha256", "transcript"},
    "call_words": {"call_id", "word_idx", "word", "start_ms", "end_ms", "confidence", "speaker"},
    "call_silences": {"call_id", "word_idx", "gap_ms", "category", "other_side_checked_in", "position_pct"},
    "call_endings": {"call_id", "ending_type", "last_step"},
    "audit_sample": {"call_id", "domain", "sample_rank"},
    "outcome_labels": {"call_id", "labeler", "prompt_version", "outcome", "confidence", "answered_by"},
}


def collect():
    """Run every check; return [(name, severity, ok, observed, expected)]. Used by run() and the dashboard."""
    results = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT table_name, array_agg(column_name::text) FROM information_schema.columns
                       WHERE table_schema = 'public' GROUP BY table_name""")
        actual = {t: set(cols) for t, cols in cur.fetchall()}
        for table, needed in EXPECTED_COLUMNS.items():
            missing = needed - actual.get(table, set())
            results.append((f"schema: {table} has expected columns", FAIL, not missing,
                            "missing: " + ", ".join(sorted(missing)) if missing else "ok", "all present"))

        for name, severity, sql, test, expectation in CHECKS:
            try:
                cur.execute(sql, {"outcomes": list(OUTCOMES)})
                value = cur.fetchone()[0]
                ok = value is not None and test(value)
            except Exception as e:  # a broken query is itself a failure (table dropped, column renamed...)
                conn.rollback()
                value, ok = f"error: {type(e).__name__}", False
            results.append((name, severity, ok, value, expectation))
    return results


def run(verbose: bool) -> int:
    results = collect()
    width = max(len(r[0]) for r in results)
    fails = warns = 0
    for name, severity, ok, value, expectation in results:
        status = "PASS" if ok else severity
        fails += status == FAIL
        warns += status == WARN
        if not ok or verbose:
            print(f"{status:<5} {name:<{width}}  observed {value}  expected {expectation}")
    passed = len(results) - fails - warns
    print(f"\n{passed} passed, {warns} warnings, {fails} failures ({len(results)} checks)")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    sys.exit(run(ap.parse_args().verbose))
