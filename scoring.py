"""
LLM judge for eval automation - one holistic score (0-10) + a comment on
what's missing/wrong. Runs on Groq (OpenAI-compatible API, plain API key) -
no Azure SDK/CLI/identity dependency at all, so this runs identically on any
machine or container with zero OS-specific credential handling.
"""
import json
import threading
from typing import Any, Dict, Optional, Tuple

from openai import OpenAI

from .config import GROQ_API_KEY, GROQ_JUDGE_MODEL

_client: Optional[OpenAI] = None
_client_lock = threading.Lock()


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    return _client


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def warm_up_client() -> Tuple[Optional[float], Optional[float]]:
    """Call once, at app startup, before any concurrent judging begins.
    Constructing the client alone doesn't open a connection (that only
    happens on first real request), so this fires one minimal real chat
    completion (max_tokens=1) against the judge model - forces the TCP/TLS
    handshake to happen now instead of on the first judged test case, and
    reads Groq's own rate-limit headers off that same call (rate limits are
    per-model, so a cheap /models list call doesn't carry them - confirmed
    live, only a real chat completion against the actual model does).
    Returns (requests_per_minute, tokens_per_minute), either None if the
    headers aren't present."""
    response = _get_client().chat.completions.with_raw_response.create(
        model=GROQ_JUDGE_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1,
    )
    requests_per_minute = _to_float(response.headers.get("x-ratelimit-limit-requests"))
    tokens_per_minute = _to_float(response.headers.get("x-ratelimit-limit-tokens"))
    return requests_per_minute, tokens_per_minute


# _JUDGE_SYSTEM_PROMPT = """You are grading a chatbot's answer against the user's question and an expected answer.
# Reply with ONLY a JSON object, no other text:
# {"score": <integer 0-10>, "comments": "<short explanation>"}

# - score: 0 = completely wrong or irrelevant, 10 = fully satisfies the user's requirement, matches
#   the expected answer's meaning, and (if an expected source was given) cites the right source.
# - comments: 1-2 short sentences. If something is missing or wrong, say exactly what. If fully
#   correct, say so briefly."""

_JUDGE_SYSTEM_PROMPT = """You are a strict academic evaluator grading a student's anwering in a open book test.

GRADING GUIDELINES:
- Act like a helpful, thorough teacher inspecting student work.
- Check if the student answer correctly matches the expected answer.
- Check if the student cited/used the correct expected sources without making up facts.
- Penalize heavily for wrong facts, missing main points, or using incorrect sources.

Respond ONLY with a valid JSON object:
{"score": <integer 0-10>, "comments": "<short explanation>"}"""


def llm_judge(
    question: str, expected_answer: str, generated_answer: str,
    expected_source: str = "", actual_sources: str = "", remarks: str = "",
) -> Dict[str, Any]:
    """One attempt, no retry here - a RateLimitError propagates to the
    caller (runner.py's _judge_row), which owns the rate limiter and retry
    policy, since that's where concurrency is actually coordinated."""

    user_input = (
        "INPUT DATA:\n"
        f"User Question: {question}\n\n"
        f"Expected Answer: {expected_answer}\n\n"
        f"Expected Sources: {expected_source or '(not provided)'}\n\n"
        f"Student Answer: {generated_answer}\n\n"
        f"Student Used Sources: {actual_sources or '(none)'}\n\n"
        f"Extra notes for grading: {remarks or '(none)'}"
    )
    response = _get_client().chat.completions.create(
        model=GROQ_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, TypeError, IndexError):
        return {"score": "", "comments": "judge call failed"}
