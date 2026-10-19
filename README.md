# CallOps: call-campaign analytics on real data

SQL funnels, production-failure detection, and an outcome audit for outbound/inbound calling,
built on two **real** public datasets:

| Module | Data | Question |
|---|---|---|
| **A. Funnel** | UCI Bank Marketing: 41,188 clients of a Portuguese bank's telemarketing campaigns, May 2008 – Nov 2010 | Who converts, and when should we stop redialling? |
| **B. Call quality** | AIxBlock CallCenterEN: 8,079 real call-center transcripts (7.87M words with timestamps and ASR confidence) | Where do calls go silent, where does speech recognition break, how do calls end? |
| **C. Outcome audit** | 200 sampled transcripts, labelled by three models and a human | Does the recorded outcome match what the transcript shows? |

> **No public dataset links leads → calls → transcripts**, so Modules A and B are deliberately kept
> separate. Nothing here fakes a join between them.

Every number below has its definition and its caveat. Most findings changed at least once after I
assumed a surprising result was my own error and checked; those corrections are listed in
[What I got wrong](#what-i-got-wrong-and-how-it-was-caught).

---

## Findings

### A. Funnel (bank telemarketing)

**1. "Conversion rate" depends on the denominator.**
Overall, 11.3% of clients subscribed (4,640 of 41,188), but only 4.39% of *calls* converted
(4,640 of 105,754 calls): 22.8 calls per conversion. Both are correct; they answer different questions
("what share of the list did we win?" vs "what does each dial buy?").

**2. Cap redials at about 5.** ([sql/a2_redial.sql](sql/a2_redial.sql))

| Call # | Conversion rate *of this call* | Calls per conversion | Conversions kept if capped here | Calls spent if capped here |
|---|---|---|---|---|
| 1 | 5.58% | 18 | 49.6% | 39.0% |
| 2 | 5.14% | 19 | 75.7% | 61.2% |
| 3 | 4.42% | 23 | 88.0% | 73.5% |
| 5 | 2.41% | 42 | **96.0%** | **85.4%** |
| 8 | 0.96% | 105 | 98.8% | 92.6% |

- *Definition:* rate of call N = clients who converted on call N ÷ clients still being called at attempt N
  (everyone with ≥ N calls, a reverse running total with a window function). The naive "conversion of
  clients who got exactly N calls" describes who you *stopped* at N, not whether call N is worth making.
- *Caveats:* assumes calling stops once a client converts (not stated in the data). Not causal: agents
  choose whom to redial. 2008 dominates and redialled hardest (up to 56 calls). A capped A/B test would settle it.

**3. Years are not comparable populations.** Conversion went from 4.8% (2008, 27,690 clients) to 19.5%
(2009, 11,440) to 52.1% (2010, 2,058). Volume fell 13× while conversion rose 10×: after the 2008
crisis the bank called fewer, warmer leads. That's selection, not a better script.
- *Caveat:* the CSV has no year column. Year is **inferred** from file order (the file is documented as
  date-ordered; months wrap Dec → Mar exactly twice) and **validated** three ways: one monthly CPI value per
  year-month, no CPI value repeated across year-months, and the Euribor rate matching the real 2008–2010 history.

**4. Cellular beats landline, but less than it looks.** Overall 14.7% vs 5.2%, but within each year the gap
is smaller (2008: 5.7% vs 3.9%; 2009: 19.9% vs 14.6%; 2010: 57.7% vs 28.4%), because the high-converting
years were mostly cellular. In May–June 2008 *every* call was a landline, so channel and month are
confounded in 2008.

**5. Past converters convert at 5.8× the average** (65.1% ± 2.5, n = 1,373). Even past *failures*
beat never-contacted clients (14.2% vs 8.8%). *Caveat:* 47% of past converters are from 2010.

### B. Call quality (transcripts)

**6. One call in five has a severe silence.** ([sql/b1_silences.sql](sql/b1_silences.sql), [b2](sql/b2_silence_impact.sql))

| Domain | Calls | Someone asked "are you there?" after a silence | Unannounced silence ≥ 30 s | Either |
|---|---|---|---|---|
| auto_insurance (inbound) | 1,748 | 13.2% | 16.2% | **26.1%** |
| auto_health_insurance (inbound) | 1,596 | 10.2% | 18.2% | 23.1% |
| medical_equipment (outbound) | 738 | 17.9% | 7.5% | 23.0% |
| insurance (outbound) | 3,997 | 11.4% | 7.1% | 15.7% |
| **All** | 8,079 | 12.2% | 11.3% | **20.1%** |

- *Definition:* silence = gap ≥ 5 s between consecutive words (`LAG()` over word timestamps; p99 of all
  gaps is 3.4 s). Each silence is classified from the 15 words before and 8 after: after a goodbye,
  transfer/IVR, announced hold ("one moment", "let me check"), or **unannounced**.
- Reading samples showed most unannounced silence is **agents silently typing quote-form answers**
  (inbound auto loses 1.1–1.3 minutes per call to it). Fix: a script line ("let me note that down").
- *Caveats:* keyword rules, not yet validated by hand. No speaker labels, so we can't say *who* went
  silent. "Are you there?" in the first 30 s is ignored (that's people answering the phone).

**7. ASR confidence is not comparable across pipelines.** ([sql/b3_asr_failures.sql](sql/b3_asr_failures.sql))
`auto_health_insurance` looked 7× worse (5.8% of words below 0.5 confidence vs ~0.8% elsewhere). The raw
files show why: its confidences have 5 decimals, the others 7–8, so it's a **different ASR export**. Humans in
those calls ask each other to repeat ("can you repeat", "can't hear you") the *least* (8.6% of calls vs
9.8–21.7%). After defining "low confidence" as the bottom 1% *within each pipeline*:

| Domain | Calls with an ASR failure stretch (3+ consecutive low words) | Calls with a human repair phrase |
|---|---|---|
| auto_insurance | 4.2% | 16.4% |
| medical_equipment | 4.1% | 21.7% |
| insurance | 2.5% | 9.8% |
| auto_health_insurance | **1.1%** | **8.6%** |

Two independent signals now rank the domains alike. Failure stretches are rare (225 in total), so these
are small counts. A hypothesis that failures cluster around personal details (names, dates of birth) was
**tested and rejected**: 19.2% of failure stretches sit near a redaction token vs a 24.3% baseline. The redacted
words themselves can't be checked.

**8. "Dead-end" endings are often the success point.** ([sql/b4_dead_ends.sql](sql/b4_dead_ends.sql))
58.5% of insurance-outbound recordings end without a goodbye, and the step most often discussed last is
Medicare eligibility (17.9% of calls reaching it). But this flow is lead generation: qualify, then hand to
a licensed agent, and the recording often stops at the handoff. **From the transcript alone, "customer
left" and "customer qualified and was transferred" look the same.** Telling them apart needs the dialler's
call disposition, which this dataset doesn't have. Real dead-end signal is clearer in auto_insurance, where the
address/zip step has a 4.0% no-closure rate vs 0.2–1.9% for other steps, the same step where Module B saw
long silent typing.

### C. Outcome audit

**Status: in progress.** Three models labelled 200 calls (50 per domain, reproducible sample) with
one shared set of 7 outcomes ([audit_config.py](audit_config.py)):

| Pair | Agreement | ± (95%) | Cohen's κ |
|---|---|---|---|
| Claude Haiku 5.5 ↔ DeepSeek v4.1 Flash | 84.5% | 5.0 | 0.79 |
| Claude Haiku 5.5 ↔ TypeSafe Jev 1.14 | 79.5% | 5.6 | 0.73 |
| DeepSeek v4.1 Flash ↔ TypeSafe Jev 1.14 | 79.5% | 5.6 | 0.73 |

All three agree on 146 of 200 calls (73%). **Operational takeaway:** route the 27% where models
disagree to human QA first.

- κ corrects agreement for chance: (observed − chance) ÷ (1 − chance), where chance = Σ P(A says k)·P(B says k).
- **Agreement between models is not accuracy**: they can share a blind spot. The human ground truth is being
  hand-labelled in a **blind** app ([app/label_calls.py](app/label_calls.py)) that never shows model labels.
  Model-vs-human results are withheld until ≥ 100 calls are labelled.
- Early human labelling already surfaced an **operational breakage**: qualified auto-insurance customers
  with **no agent available to transfer to** (5 of the first 48 labelled calls).
- Jev was called through OpenRouter's Decisions API (one `choice` question; it returns a probability per
  outcome). `typesafe/jev-router` on the chat API is a different product: it picks a chat model (GLM in
  testing), so it was not used.

---

## What I got wrong and how it was caught

| Wrong first | Caught by | Fix |
|---|---|---|
| "This call may be recorded" counted as dead air | Reading samples | IVR patterns added |
| Indian-English "please be online" missed as a hold cue | Reading samples | Pattern added |
| "Hello? Hello?" at pickup counted as noticed dead air (14.3% → 12.2%) | Odd spike in the first 10% of calls | Ignore the first 30 s |
| One domain "7× worse" ASR | Raw file format + human repair phrases | Per-pipeline thresholds |
| Insurance outbound "62% abrupt endings" | Reading endings: handoffs, not hang-ups | Renamed `no_closure`, caveated |
| `jev-router` treated as Jev | Checked which model answered | Switched to the Decisions API |
| 204 duplicate transcripts (`file_(2)_transcript.json`) | Reconciliation counts in the loader | SHA-256 dedup |

---

## Data quality

[`checks.py`](checks.py) runs **32 SQL assertions** in ~3 s and exits non-zero on any FAIL (so a scheduler or CI job
can alert): row counts, the `pdays = 999` sentinel, year inference, duplicates, word timing, derived-table
invariants, audit label validity, and schema drift. Known quirks are WARNs with tolerances (79 overlapping
ASR timestamps, 12 identical bank rows kept on purpose because there is no client id). The checks were
themselves tested by breaking the data inside a rolled-back transaction.

## Dashboard

```bash
streamlit run app/dashboard.py --server.port 8502 --server.address localhost
```
Eight tabs (funnel, redial, channel & timing, silences, ASR, endings, audit, data quality). Module A tabs run the
same `.sql` files as the analysis, and every rate is shown with its n and caveat.

## Reproduce

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
docker run -d --name callops-pg -e POSTGRES_USER=callops -e POSTGRES_PASSWORD=callops \
  -e POSTGRES_DB=callops -p 5432:5432 -v callops-pgdata:/var/lib/postgresql/data postgres:17
python download_data.py          # ~1.4 GB into data/ (gitignored)
python load_bank.py              # bank_contacts
python load_transcripts.py       # calls, call_words (insurance-heavy subset, deduplicated)
for f in sql/b1_silences.sql sql/b3_asr_failures.sql sql/b4_dead_ends.sql sql/c1_audit_tables.sql; do
  docker exec -i callops-pg psql -U callops < $f; done
python checks.py
```
Model labelling needs `OPENROUTER_API_KEY` in `.env` (never committed): `python label_llm.py --model <id>`
or `--provider jev --model typesafe/jev-1.14`. Cost for 200 calls: cents per model.

## Layout

```
download_data.py  load_bank.py  load_transcripts.py   data in (data/ is gitignored)
sql/01-02_*      schemas, with cleaning decisions documented
sql/a1-a4        Module A: funnel, redial, channel/timing, history
sql/b1-b4        Module B: silences, impact, ASR failures, endings
sql/c1-c2        Module C: audit sample, results (agreement, kappa, confusion matrix)
audit_config.py  the one outcome definition shared by humans and models
label_llm.py     model labeller (chat models or Jev)
app/             label_calls.py (blind labelling), dashboard.py
checks.py        automated data-quality checks
```

## Limitations

- The two datasets can't be joined, so there is no end-to-end lead → transcript funnel.
- Bank data is 2008–2010 Portugal, contact-level (not call-level), with an inferred year.
- Transcripts have no speaker labels and no accent tags (despite the dataset card), short calls appear
  filtered out upstream (only 1.7% are under 3 minutes), and personal details are redacted.
- Silence and ending categories are keyword rules, not yet validated against hand labels.
- The human ground truth is one labeller, still in progress.

## Data & license

- UCI Bank Marketing: Moro, Cortez & Rita (2014), *Decision Support Systems*. [UCI](https://archive.ics.uci.edu/dataset/222/bank+marketing)
- AIxBlock CallCenterEN: [Hugging Face](https://huggingface.co/datasets/AIxBlock/92k-real-world-call-center-scripts-english), CC BY-NC 4.0,
  research use only. **No raw data, transcripts, or labels are in this repo.** Source filenames (some look like phone
  numbers) are never displayed.
