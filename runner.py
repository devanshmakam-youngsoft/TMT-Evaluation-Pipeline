"""Runs one CSV of test cases against a real backend (local or deployed) over
plain HTTP - the same /foundry/generate (and, for pipeline_path=regeneration,
/foundry/retry) endpoints the real chat UI calls, just scripted. Each input
row produces 1 output row (pipeline_path=generation, the default) or 2
(pipeline_path=regeneration - one for the generate call, one for the retry).

`version` isn't a column in the input file - it's supplied by the caller at
run time (watch_and_run.py derives it from the CSV's own filename) and
stamped onto every output row for that run."""
import time
import uuid
from typing import Any, Dict, List

import requests

from .scoring import llm_judge

_EVAL_USER_ID = "eval-runner"


def _call_generate(base_url: str, token: str, question: str) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    url = f"{base_url}/foundry/users/{_EVAL_USER_ID}/generate"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"user_input": question, "session_id": session_id, "message_id": message_id, "title": "eval"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _call_retry(base_url: str, token: str, session_id: str, question_message_id: str) -> Dict[str, Any]:
    url = f"{base_url}/foundry/users/{_EVAL_USER_ID}/retry"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": session_id,
            "question_message_id": question_message_id,
            "client_attempt_id": str(uuid.uuid4()),
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _base_fields(version: str, row: Dict[str, str]) -> Dict[str, str]:
    return {
        "version": version,
        "type": (row.get("type") or "").strip(),
        "question": (row.get("question") or "").strip(),
        "expected_answer": (row.get("expected_answer") or "").strip(),
        "expected_source": (row.get("expected_source") or "").strip(),
        "remarks": (row.get("remarks") or "").strip(),
    }


def _build_row(
    base: Dict[str, str], call_type: str, generated_answer: str, actual_sources: str,
    time_taken: Any, error: str = "",
) -> Dict[str, Any]:
    result_row = {
        **base, "call_type": call_type, "generated_answer": generated_answer,
        "actual_sources": actual_sources, "llm_score": "", "llm_comments": "",
        "time_taken_seconds": time_taken, "error": error,
    }
    if not error and generated_answer:
        judge = llm_judge(
            base["question"], base["expected_answer"], generated_answer,
            base["expected_source"], actual_sources, base["remarks"],
        )
        result_row["llm_score"] = judge.get("score", "")
        result_row["llm_comments"] = judge.get("comments", "")
    return result_row


def run_test_case(base_url: str, token: str, version: str, row: Dict[str, str]) -> List[Dict[str, Any]]:
    base = _base_fields(version, row)
    pipeline_path = (row.get("pipeline_path") or "generation").strip().lower()

    if not base["question"] or not base["expected_answer"]:
        return [_build_row(base, pipeline_path, "", "", "", "missing question or expected_answer - skipped")]

    start = time.perf_counter()
    try:
        gen_response = _call_generate(base_url, token, base["question"])
    except Exception as exc:
        return [_build_row(base, "generation", "", "", round(time.perf_counter() - start, 2), f"generate call failed: {exc}")]

    if not gen_response.get("success"):
        return [_build_row(base, "generation", "", "", round(time.perf_counter() - start, 2), gen_response.get("error") or "generate returned success=false")]

    generated_answer = gen_response.get("answer") or ""
    actual_sources = ", ".join(s.get("filename", "?") for s in (gen_response.get("sources") or []))
    # The API's own reported total_time_taken - not a client-side stopwatch
    # around the HTTP call (that would include network/serialization
    # overhead this endpoint itself doesn't count).
    time_taken = gen_response.get("total_time_taken")
    results = [_build_row(base, "generation", generated_answer, actual_sources, time_taken)]

    if pipeline_path != "regeneration":
        return results

    start2 = time.perf_counter()
    try:
        retry_response = _call_retry(
            base_url, token, gen_response.get("session_id"), gen_response.get("question_message_id"),
        )
    except Exception as exc:
        results.append(_build_row(base, "regeneration", "", "", round(time.perf_counter() - start2, 2), f"retry call failed: {exc}"))
        return results

    if not retry_response.get("success"):
        results.append(_build_row(base, "regeneration", "", "", round(time.perf_counter() - start2, 2), retry_response.get("error") or "retry returned success=false"))
        return results

    regenerated_answer = retry_response.get("answer") or ""
    regenerated_sources = ", ".join(s.get("filename", "?") for s in (retry_response.get("sources") or []))
    time_taken2 = retry_response.get("total_time_taken")
    results.append(_build_row(base, "regeneration", regenerated_answer, regenerated_sources, time_taken2))
    return results


def run_test_cases(base_url: str, token: str, version: str, rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    all_results: List[Dict[str, Any]] = []
    for row in rows:
        all_results.extend(run_test_case(base_url, token, version, row))
    return all_results
