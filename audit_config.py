"""
Outcome audit: the ONE label set shared by the human labeller (app/label_calls.py) and the LLM
labeller (label_llm.py). If the two used different definitions, agreement would be meaningless.
"""

# Bump this whenever OUTCOMES or the prompt changes, so old and new LLM labels are never mixed.
PROMPT_VERSION = "v1"

OUTCOMES = {
    "converted":   "Customer committed: bought, enrolled, booked, ordered, or agreed to the offer.",
    "transferred": "Customer was handed to another agent/department; the final outcome is not in this recording.",
    "callback":    "A follow-up was agreed (call back, send info) but no decision was made yet.",
    "declined":    "Customer said no / not interested / asked not to be called.",
    "resolved":    "Service call: the customer's question or issue was handled.",
    "unresolved":  "Service call: the issue was NOT handled and the customer still needs help.",
    "incomplete":  "The call ends before any outcome is visible (cut off, dropped, wrong person, voicemail).",
}

PROMPT = """You are auditing a call-center transcript. Decide the call's OUTCOME.

The transcript comes from speech recognition: no speaker labels, some words misheard, and personal
details replaced by tokens like [PERSON_NAME]. Judge only from what is in the transcript.

Allowed outcomes (pick exactly one):
{outcomes}

Reply with JSON only, no prose:
{{"outcome": "<one of: {keys}>", "confidence": <0.0-1.0>, "evidence": "<short quote from the transcript>"}}

Transcript:
\"\"\"
{transcript}
\"\"\"
"""


def build_prompt(transcript: str) -> str:
    return PROMPT.format(
        outcomes="\n".join(f"- {k}: {v}" for k, v in OUTCOMES.items()),
        keys=", ".join(OUTCOMES),
        transcript=transcript,
    )
