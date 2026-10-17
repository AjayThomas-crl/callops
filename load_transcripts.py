"""
Load a subset of AIxBlock CallCenterEN transcripts into Postgres (calls, call_words).

Reads JSON straight out of the zips (no extraction), deduplicates by content hash,
and is defensive about schema drift: anything malformed is counted and skipped, never guessed.

Run:  python load_transcripts.py
Re-runnable: drops and recreates both tables each time.
"""
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

from db import connect

DATA = Path("data/callcenter")
SCHEMA = Path("sql/02_transcripts_schema.sql")

# zip file -> (domain, direction). Insurance-heavy subset: closest to BFSI.
SUBSET = {
    "insurance_outbound.zip": ("insurance", "outbound"),
    "automotive_and_healthcare_insurance_inbound.zip": ("auto_health_insurance", "inbound"),
    "auto_insurance_customer_service_inbound.zip": ("auto_insurance", "inbound"),
    "medical_equipment_outbound.zip": ("medical_equipment", "outbound"),
}


def as_int(v):
    """Timestamps should be ints; accept floats/numeric strings, reject anything else."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            return None
    return None


def as_float(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def clean_words(raw_words):
    """Return (valid words, number rejected). A word needs text and start <= end timestamps."""
    words, rejected = [], 0
    for w in raw_words if isinstance(raw_words, list) else []:
        if not isinstance(w, dict):
            rejected += 1
            continue
        start, end, text = as_int(w.get("start")), as_int(w.get("end")), w.get("text")
        if start is None or end is None or end < start or not isinstance(text, str):
            rejected += 1
            continue
        speaker = w.get("speaker")
        words.append((text, start, end, as_float(w.get("confidence")), None if speaker is None else str(speaker)))
    return words, rejected


def iter_calls(stats: Counter):
    """Yield parsed calls from the subset zips in a deterministic order."""
    for zip_name, (domain, direction) in SUBSET.items():
        with zipfile.ZipFile(DATA / zip_name) as zf:
            # __MACOSX/ holds macOS metadata files, not transcripts
            names = sorted(n for n in zf.namelist() if n.endswith(".json") and not n.startswith("__MACOSX"))
            for name in names:
                stats["files_seen"] += 1
                raw = zf.read(name)
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    stats["bad_json"] += 1
                    continue
                if not isinstance(obj, dict):
                    stats["not_an_object"] += 1
                    continue
                words, rejected = clean_words(obj.get("words"))
                if not words:
                    stats["no_valid_words"] += 1
                    continue
                stats["words_rejected"] += rejected
                yield {
                    "source_zip": zip_name,
                    "source_file": name,
                    "sha": hashlib.sha256(raw).hexdigest(),
                    "domain": domain,
                    "direction": direction,
                    "audio_duration_s": as_int(obj.get("audio_duration")),
                    "asr_confidence": as_float(obj.get("confidence")),
                    "transcript": obj.get("text") if isinstance(obj.get("text"), str) else " ".join(w[0] for w in words),
                    "words": words,
                    "words_rejected": rejected,
                }


def main() -> None:
    stats = Counter()
    seen_sha = set()
    call_id = 0

    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text())

        # Two COPY streams can't be open on one connection at once, so buffer call rows
        # (small) and stream word rows (large) directly.
        call_rows = []
        with cur.copy("COPY call_words (call_id, word_idx, word, start_ms, end_ms, confidence, speaker) FROM STDIN") as copy:
            for c in iter_calls(stats):
                if c["sha"] in seen_sha:
                    stats["duplicates_skipped"] += 1
                    continue
                seen_sha.add(c["sha"])
                call_id += 1
                words = c["words"]
                for i, (text, start, end, conf, speaker) in enumerate(words):
                    copy.write_row((call_id, i, text, start, end, conf, speaker))
                    if speaker is not None:
                        stats["words_with_speaker"] += 1
                stats["words_loaded"] += len(words)
                call_rows.append((
                    call_id, c["source_zip"], c["source_file"], c["sha"], c["domain"], c["direction"],
                    c["audio_duration_s"], c["asr_confidence"], len(words), c["words_rejected"],
                    words[0][1], words[-1][2], c["transcript"],
                ))
                if call_id % 1000 == 0:
                    print(f"  {call_id:,} calls...")

        with cur.copy(
            "COPY calls (call_id, source_zip, source_file, content_sha256, domain, direction, audio_duration_s,"
            " asr_confidence, n_words, n_words_rejected, first_word_ms, last_word_ms, transcript) FROM STDIN"
        ) as copy:
            for row in call_rows:
                copy.write_row(row)

        print("building keys and indexes...")
        cur.execute("""
            ALTER TABLE call_words ADD PRIMARY KEY (call_id, word_idx);
            ALTER TABLE call_words ADD FOREIGN KEY (call_id) REFERENCES calls (call_id);
            CREATE INDEX ON calls (domain, direction);
            ANALYZE calls;
            ANALYZE call_words;
        """)

    print(f"\nloaded {call_id:,} calls, {stats['words_loaded']:,} words")
    for k in ["files_seen", "duplicates_skipped", "bad_json", "not_an_object", "no_valid_words",
              "words_rejected", "words_with_speaker"]:
        print(f"  {k:<20} {stats[k]:,}")


if __name__ == "__main__":
    main()
