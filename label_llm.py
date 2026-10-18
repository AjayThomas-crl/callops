"""
Label call outcomes in the audit sample with an LLM (the "recorded outcome" side of the audit).

Two kinds of labeller:
  * chat LLMs via any OpenAI-compatible API (OpenRouter, DeepSeek): prompt in, JSON reply out.
  * Jev (TypeSafe's decision model) via OpenRouter's Decisions API: transcript as `state`, one
    `choice` question whose criteria are the OUTCOMES. Returns a probability per outcome; no prompt
    text is generated. NOTE: "typesafe/jev-router" on the chat API is a different product (Jev picks
    a chat model, which then answers), so it is NOT Jev labelling the call.

Configure in .env (gitignored, never commit keys):
    OPENROUTER_API_KEY=...          (or DEEPSEEK_API_KEY=...)

Run:
    python label_llm.py --model deepseek/deepseek-v4.1-flash          # chat LLM via OpenRouter
    python label_llm.py --provider deepseek --model deepseek-chat     # DeepSeek direct
    python label_llm.py --provider jev --model typesafe/jev-1.14      # Jev via Decisions API
    python label_llm.py --model <model> --limit 5                     # try a few first

Re-runnable: calls that already have a label from this model + prompt version are skipped.
Temperature 0 so reruns give (near-)identical labels.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

from audit_config import OUTCOMES, PROMPT_VERSION, build_prompt
from db import connect

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "jev": ("https://openrouter.ai/api/alpha/decisions", "OPENROUTER_API_KEY"),
}

# Same context the chat LLMs get in their prompt, phrased as Jev's single question.
JEV_INSTRUCTIONS = (
    "What is the outcome of this call-center call? The transcript comes from speech recognition: "
    "no speaker labels, some words misheard, and personal details replaced by tokens like [PERSON_NAME]. "
    "Judge only from what is in the transcript."
)


def parse(reply: str):
    """Pull the JSON object out of the reply; models sometimes wrap it in ``` fences."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    outcome = str(data.get("outcome", "")).strip().lower()
    if outcome not in OUTCOMES:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    return outcome, confidence, str(data.get("evidence", ""))[:500]


def ask_chat(client, model, transcript):
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": build_prompt(transcript)}],
    )
    reply = resp.choices[0].message.content or ""
    return parse(reply), reply, resp.model  # resp.model: routers may hand the call to another model


def ask_jev(api_key, model, transcript):
    resp = requests.post(
        PROVIDERS["jev"][0],
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "state": {"transcript": transcript},
            "questions": {"outcome": {"type": "choice", "instructions": JEV_INSTRUCTIONS, "criteria": OUTCOMES}},
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    answer = data["answers"]["outcome"]
    parsed = None
    if answer.get("choice") in OUTCOMES:
        # evidence = the full probability distribution: Jev gives no quote, but this shows how torn it was
        parsed = (answer["choice"], answer.get("confidence"), json.dumps(answer.get("probabilities", {})))
    return parsed, json.dumps(data), data.get("model", model)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=PROVIDERS, default="openrouter")
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parent / ".env")
    base_url, key_var = PROVIDERS[args.provider]
    api_key = os.environ.get(key_var)
    if not api_key:
        raise SystemExit(f"{key_var} is not set. Add it to .env")
    client = None if args.provider == "jev" else OpenAI(base_url=base_url, api_key=api_key)
    labeler = f"llm:{args.model}"

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.call_id, c.transcript
            FROM audit_sample s JOIN calls c USING (call_id)
            WHERE NOT EXISTS (SELECT 1 FROM outcome_labels l
                              WHERE l.call_id = s.call_id AND l.labeler = %s AND l.prompt_version = %s)
            ORDER BY s.sample_rank, s.domain
        """, (labeler, PROMPT_VERSION))
        todo = cur.fetchall()[: args.limit]

    print(f"{labeler} | prompt {PROMPT_VERSION} | {len(todo)} calls to label")
    failed = 0
    for i, (call_id, transcript) in enumerate(todo, 1):
        for attempt in range(3):
            try:
                if args.provider == "jev":
                    parsed, reply, answered_by = ask_jev(api_key, args.model, transcript)
                else:
                    parsed, reply, answered_by = ask_chat(client, args.model, transcript)
                break
            except Exception as e:  # network / rate limit: back off and retry
                print(f"  call {call_id}: {type(e).__name__}, retry {attempt + 1}")
                time.sleep(2 ** attempt * 5)
        else:
            failed += 1
            continue

        if parsed is None:
            # Record nothing rather than guess; the raw reply is printed for debugging.
            failed += 1
            print(f"  call {call_id}: could not parse reply: {reply[:120]!r}")
            continue
        outcome, confidence, evidence = parsed
        with connect() as conn:
            conn.execute("""
                INSERT INTO outcome_labels (call_id, labeler, prompt_version, outcome, confidence, evidence,
                                            raw_response, answered_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_id, labeler) DO UPDATE
                SET prompt_version = EXCLUDED.prompt_version, outcome = EXCLUDED.outcome,
                    confidence = EXCLUDED.confidence, evidence = EXCLUDED.evidence,
                    raw_response = EXCLUDED.raw_response, answered_by = EXCLUDED.answered_by, labeled_at = now()
            """, (call_id, labeler, PROMPT_VERSION, outcome, confidence, evidence, reply, answered_by))
        routed = "" if answered_by.startswith(args.model) else f" [answered by {answered_by}]"
        print(f"  [{i}/{len(todo)}] call {call_id}: {outcome} ({confidence}){routed}")

    print(f"done. failed/unparsed: {failed}")


if __name__ == "__main__":
    main()
