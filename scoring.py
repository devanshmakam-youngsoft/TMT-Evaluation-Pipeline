"""
LLM judge for eval automation - one holistic score (0-10) + a comment on
what's missing/wrong. Runs on Groq (OpenAI-compatible API, plain API key) -
no Azure SDK/CLI/identity dependency at all, so this runs identically on any
machine or container with zero OS-specific credential handling.
"""
import json
from typing import Any, Dict, Optional

from openai import OpenAI

from .config import GROQ_API_KEY, GROQ_JUDGE_MODEL

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    return _client


_JUDGE_SYSTEM_PROMPT = """You are grading a chatbot's answer against the user's question and an expected answer.
Reply with ONLY a JSON object, no other text:
{"score": <integer 0-10>, "comments": "<short explanation>"}

- score: 0 = completely wrong or irrelevant, 10 = fully satisfies the user's requirement, matches
  the expected answer's meaning, and (if an expected source was given) cites the right source.
- comments: 1-2 short sentences. If something is missing or wrong, say exactly what. If fully
  correct, say so briefly."""


def llm_judge(
    question: str, expected_answer: str, generated_answer: str,
    expected_source: str = "", actual_sources: str = "", remarks: str = "",
) -> Dict[str, Any]:
    user_input = (
        f"Question: {question}\n\n"
        f"Expected answer: {expected_answer}\n\n"
        f"Chatbot's actual answer: {generated_answer}\n\n"
        f"Expected source: {expected_source or '(not provided)'}\n\n"
        f"Chatbot's actual source(s): {actual_sources or '(none)'}\n\n"
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
