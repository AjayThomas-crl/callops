"""
Hand-label call outcomes for the audit (ground truth).

Run:  streamlit run app/label_calls.py

Blind by design: the LLM's label is never shown here, so the human label cannot anchor on it.
Never shows source_file either (some filenames look like phone numbers).

Password: set LABEL_APP_PASSWORD in .env. Required, because once the app is reachable from a phone it
is reachable by anyone on the same network, and a stranger's clicks would land in the ground truth.
"""
import hmac
import html
import os
import re
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from audit_config import OUTCOMES, PROMPT_VERSION  # noqa: E402
from db import connect  # noqa: E402

LABELER = "human"
TAIL_MS = 180_000      # show the last 3 minutes first: outcomes are usually at the end
TURN_GAP_MS = 1_000    # a pause this long usually means the other person is talking -> new line
SILENCE_MS = 5_000     # same threshold as b1_silences
# Bottom 5% of real-word confidence in each ASR pipeline (see sql/b3_asr_failures.sql):
# the scales differ, so one fixed cut-off would fade far too much of auto_health_insurance.
LOW_CONF = {"A": 0.744, "B": 0.474}
REDACTION = re.compile(r"^\[([A-Z_]+)\]([^\w\s]*)$")   # "[PERSON_NAME]," -> ("PERSON_NAME", ",")

CSS = """
<style>
.tx {font-size: 1.05rem; line-height: 1.65;}
.tx .ln {margin: 0 0 .55rem 0;}
.tx .ts {font-family: monospace; font-size: .8rem; opacity: .55; margin-right: .5rem;}
.tx .lc {opacity: .45; font-style: italic;}
.tx .pii {font-size: .72rem; padding: .05rem .35rem; border-radius: .3rem;
          background: rgba(128,128,128,.18); opacity: .8; white-space: nowrap;}
.tx .sil {font-size: .85rem; opacity: .6; margin: .2rem 0 .7rem 0; border-top: 1px dashed rgba(128,128,128,.5);}
</style>
"""

st.set_page_config(page_title="CallOps outcome labelling", layout="wide")

load_dotenv(ROOT / ".env")
PASSWORD = os.environ.get("LABEL_APP_PASSWORD")
if not PASSWORD:
    st.error("Set LABEL_APP_PASSWORD in .env, then restart the app.")
    st.stop()
if not st.session_state.get("authenticated"):
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, PASSWORD):  # constant-time compare
        st.session_state.authenticated = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()


def query(sql, params=()):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def progress():
    rows = query("""
        SELECT s.domain, count(l.call_id), count(*)
        FROM audit_sample s
        LEFT JOIN outcome_labels l ON l.call_id = s.call_id AND l.labeler = %s
        GROUP BY s.domain ORDER BY s.domain
    """, (LABELER,))
    return rows


def next_call(skipped):
    # rank order interleaves domains, so stopping early keeps the sample balanced
    row = query("""
        SELECT s.call_id, s.domain, s.sample_rank, c.audio_duration_s
        FROM audit_sample s JOIN calls c USING (call_id)
        WHERE NOT EXISTS (SELECT 1 FROM outcome_labels l WHERE l.call_id = s.call_id AND l.labeler = %s)
          AND NOT (s.call_id = ANY(%s))
        ORDER BY s.sample_rank, s.domain
        LIMIT 1
    """, (LABELER, list(skipped)))
    return row[0] if row else None


def load_words(call_id):
    return query("SELECT word, start_ms, end_ms, confidence FROM call_words WHERE call_id = %s ORDER BY word_idx",
                 (call_id,))


def ts(ms):
    sec = ms // 1000
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render(words, low_conf):
    """Rebuild a readable transcript from word timings: a new line at each pause (roughly a turn
    change; speakers are NOT known), silence dividers, collapsed redaction tags, faded low-confidence
    words. Everything is HTML-escaped: transcript text is data, never markup."""
    out, line, line_start, prev_end, prev_tag = [], [], None, None, None

    def flush():
        if line:
            out.append(f'<div class="ln"><span class="ts">{ts(line_start)}</span>{" ".join(line)}</div>')

    for word, start, end, conf in words:
        gap = 0 if prev_end is None else start - prev_end
        long_line = len(line) > 45 and re.sub(r"<[^>]+>", "", line[-1]).endswith((".", "?", "!"))
        if prev_end is None or gap >= TURN_GAP_MS or long_line:
            flush()
            if gap >= SILENCE_MS:
                out.append(f'<div class="sil">⏸ {gap / 1000:.0f} s silence</div>')
            line, line_start, prev_tag = [], start, None
        prev_end = end

        m = REDACTION.match(word)
        if m:
            tag, punct = m.groups()
            if tag == prev_tag:                     # [ORG] [ORG] [ORG] -> one tag
                if punct:
                    line[-1] += html.escape(punct)
                continue
            line.append(f'<span class="pii">{tag.lower().replace("_", " ")}</span>{html.escape(punct)}')
            prev_tag = tag
            continue
        prev_tag = None
        text = html.escape(word)
        line.append(f'<span class="lc">{text}</span>' if conf is not None and conf < low_conf else text)
    flush()
    return '<div class="tx">' + "".join(out) + "</div>"


def save(call_id, outcome, notes):
    query("""
        INSERT INTO outcome_labels (call_id, labeler, prompt_version, outcome, notes)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (call_id, labeler) DO UPDATE
        SET outcome = EXCLUDED.outcome, notes = EXCLUDED.notes, labeled_at = now()
    """, (call_id, LABELER, PROMPT_VERSION, outcome, notes or None))


st.session_state.setdefault("skipped", set())

# Sidebar: progress + definitions
with st.sidebar:
    st.header("Progress")
    total_done = total = 0
    for domain, done, n in progress():
        st.progress(done / n, text=f"{domain}: {done}/{n}")
        total_done, total = total_done + done, total + n
    st.caption(f"{total_done}/{total} labelled. Stopping at 25 per domain still gives a balanced sample.")
    st.header("Definitions")
    for k, v in OUTCOMES.items():
        st.markdown(f"**{k}**: {v}")

call = next_call(st.session_state.skipped)
if call is None:
    st.success("All sampled calls are labelled (or skipped). Restart the app to revisit skipped ones.")
    st.stop()

call_id, domain, rank, duration_s = call
st.subheader(f"Call {call_id} · {domain} · {duration_s // 60}m {duration_s % 60}s · sample #{rank}")

words = load_words(call_id)
low_conf = LOW_CONF["B" if domain == "auto_health_insurance" else "A"]
st.markdown(CSS, unsafe_allow_html=True)
st.caption("New line = a pause (speakers are not known) · faded = ASR unsure · grey tag = redacted detail")

show_full = st.toggle("Show full call", key=f"full-{call_id}")
cutoff = 0 if show_full else max(0, words[-1][2] - TAIL_MS)
shown = [w for w in words if w[1] >= cutoff]
if cutoff:
    st.markdown(f"**Last 3 minutes** · {ts(cutoff)} → {ts(words[-1][2])} · flip the toggle above for the rest")
st.markdown(render(shown, low_conf), unsafe_allow_html=True)

with st.form(key=f"label-{call_id}", clear_on_submit=True):
    outcome = st.radio("Outcome", list(OUTCOMES), index=None, horizontal=True,
                       format_func=lambda k: k, help="See definitions in the sidebar")
    notes = st.text_input("Notes (optional): why, or what made it ambiguous")
    col1, col2 = st.columns(2)
    submitted = col1.form_submit_button("Save & next", type="primary")
    skipped = col2.form_submit_button("Skip for now")

if submitted:
    if outcome is None:
        st.error("Pick an outcome first.")
    else:
        save(call_id, outcome, notes)
        st.rerun()
if skipped:
    st.session_state.skipped.add(call_id)
    st.rerun()
