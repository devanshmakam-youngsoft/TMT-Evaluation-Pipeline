"""Runs one CSV of test cases against a real backend (local or deployed) over
plain HTTP - the same /foundry/generate (and, for type=query, /foundry/retry
right after) endpoints the real chat UI calls, just scripted. type=query
always produces 2 output rows (one generate, one regenerate); any other type
(e.g. conversation) produces just 1 (generate only) - not driven by an input
column, no pipeline_path field in the CSV at all.

Parallelized in two decoupled, pipelined stages:
  - generation pool (GENERATION_MAX_WORKERS): one task per input row, doing
    the real HTTP calls. Hits the user's own deployed dev backend - real
    Foundry agent calls, real cost, and that backend's own internal Azure
    OpenAI deployments have their own configured capacity, so pushing this
    too high can trip 429s *inside* the graph (surfaces as a row's `error`
    field, not a crash - worth watching if failures cluster).
  - judge pool (JUDGE_MAX_WORKERS), gated by a RateLimiter - judge calls are
    fast enough that worker *count* alone doesn't cap request *rate*, so an
    actual token-bucket limiter paces dispatch to the real Groq limit
    (detected via scoring.warm_up_client(), falling back to a safe default).

Each input row's ungraded HTTP result(s) are handed to the judge stage the
moment they're ready, not gated behind every row finishing generation first.

`version` isn't a column in the input file either - it's supplied by the
caller at run time (gui_run.py) and stamped onto every output row for that
run."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

import requests
from openai import APIError, RateLimitError

from .scoring import llm_judge

_EVAL_USER_ID = "eval-runner"

GENERATION_MAX_WORKERS = 20
JUDGE_MAX_WORKERS = 8
JUDGE_MAX_ATTEMPTS = 3

# Fallback when Groq's rate-limit headers aren't available - safely under
# Groq's documented free-tier ceiling (30 RPM) for openai/gpt-oss-20b.
DEFAULT_JUDGE_RATE_LIMIT_RPM = 25.0
# Rough estimate: judge system prompt + question/expected/generated/sources
# + a short JSON reply - used only to derive a token-budget-based pace.
ESTIMATED_TOKENS_PER_JUDGE_CALL = 400.0


class RateLimiter:
    """Simple thread-safe pacing limiter: spaces out acquire() calls so no
    more than `requests_per_minute` happen in any given minute, regardless
    of how many threads are contending for it. This is what actually caps
    throughput - a bounded worker pool alone does not, since fast calls can
    still fire far more often than once per (60/limit) seconds."""

    def __init__(self, requests_per_minute: float) -> None:
        self._interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = time.monotonic()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._interval


def derive_judge_rate_limit(requests_per_minute: Optional[float], tokens_per_minute: Optional[float]) -> float:
    """Groq enforces both RPM and TPM simultaneously - use whichever derived
    pace is more conservative (slower)."""
    candidates = []
    if requests_per_minute:
        candidates.append(requests_per_minute)
    if tokens_per_minute:
        candidates.append(tokens_per_minute / ESTIMATED_TOKENS_PER_JUDGE_CALL)
    return min(candidates) if candidates else DEFAULT_JUDGE_RATE_LIMIT_RPM


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


def _base_fields(version: str, slno: int, row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "slno": slno,
        "version": version,
        "type": (row.get("type") or "").strip(),
        "question": (row.get("question") or "").strip(),
        "expected_answer": (row.get("expected_answer") or "").strip(),
        "expected_source": (row.get("expected_source") or "").strip(),
        "remarks": (row.get("remarks") or "").strip(),
    }


def _build_ungraded_row(
    base: Dict[str, Any], pipeline_path: str, generated_answer: str, actual_sources: str,
    time_taken: Any, error: str = "",
) -> Dict[str, Any]:
    return {
        **base, "pipeline_path": pipeline_path, "generated_answer": generated_answer,
        "actual_sources": actual_sources, "llm_score": "", "llm_comments": "",
        "time_taken_seconds": time_taken, "error": error,
    }


def _extract_retry_after(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _judge_row(row: Dict[str, Any], rate_limiter: RateLimiter) -> Dict[str, Any]:
    if row["error"] or not row["generated_answer"]:
        return row

    for attempt in range(JUDGE_MAX_ATTEMPTS):
        rate_limiter.acquire()
        try:
            judge = llm_judge(
                row["question"], row["expected_answer"], row["generated_answer"],
                row["expected_source"], row["actual_sources"], row["remarks"],
            )
            row["llm_score"] = judge.get("score", "")
            row["llm_comments"] = judge.get("comments", "")
            return row
        except RateLimitError as exc:
            time.sleep(_extract_retry_after(exc) or (2 ** attempt))
        except APIError as exc:
            # e.g. BadRequestError when the model fails to produce valid
            # JSON for a particular input - a real, occasional Groq failure
            # mode seen at scale, not a rate-limit issue. Not worth retrying
            # (usually deterministic for the same input) - fail just this
            # row's score, never let it crash the whole run.
            row["llm_comments"] = f"judge call failed: {exc}"
            return row

    row["llm_comments"] = "judge call failed after retries (rate limited)"
    return row


def run_test_case(
    base_url: str, token: str, version: str, slno: int, row: Dict[str, str],
    on_ungraded_row: Callable[[Dict[str, Any]], None],
) -> None:
    base = _base_fields(version, slno, row)
    is_query = base["type"].lower() == "query"

    if not base["question"] or not base["expected_answer"]:
        on_ungraded_row(_build_ungraded_row(base, "generation", "", "", "", "missing question or expected_answer - skipped"))
        return

    start = time.perf_counter()
    try:
        gen_response = _call_generate(base_url, token, base["question"])
    except Exception as exc:
        on_ungraded_row(_build_ungraded_row(base, "generation", "", "", round(time.perf_counter() - start, 2), f"generate call failed: {exc}"))
        return

    if not gen_response.get("success"):
        on_ungraded_row(_build_ungraded_row(base, "generation", "", "", round(time.perf_counter() - start, 2), gen_response.get("error") or "generate returned success=false"))
        return

    generated_answer = gen_response.get("answer") or ""
    actual_sources = ", ".join(s.get("filename", "?") for s in (gen_response.get("sources") or []))
    # The API's own reported total_time_taken - not a client-side stopwatch
    # around the HTTP call (that would include network/serialization
    # overhead this endpoint itself doesn't count).
    on_ungraded_row(_build_ungraded_row(base, "generation", generated_answer, actual_sources, gen_response.get("total_time_taken")))

    if not is_query:
        return

    start2 = time.perf_counter()
    try:
        retry_response = _call_retry(
            base_url, token, gen_response.get("session_id"), gen_response.get("question_message_id"),
        )
    except Exception as exc:
        on_ungraded_row(_build_ungraded_row(base, "regeneration", "", "", round(time.perf_counter() - start2, 2), f"retry call failed: {exc}"))
        return

    if not retry_response.get("success"):
        on_ungraded_row(_build_ungraded_row(base, "regeneration", "", "", round(time.perf_counter() - start2, 2), retry_response.get("error") or "retry returned success=false"))
        return

    regenerated_answer = retry_response.get("answer") or ""
    regenerated_sources = ", ".join(s.get("filename", "?") for s in (retry_response.get("sources") or []))
    on_ungraded_row(_build_ungraded_row(base, "regeneration", regenerated_answer, regenerated_sources, retry_response.get("total_time_taken")))


def run_test_cases(
    base_url: str, token: str, version: str, rows: List[Dict[str, str]],
    judge_rate_limit_rpm: Optional[float] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    rate_limiter = RateLimiter(judge_rate_limit_rpm or DEFAULT_JUDGE_RATE_LIMIT_RPM)

    # query rows produce 2 rows, others 1 - known ahead of time from `type`,
    # used only to make progress messages meaningful.
    estimated_total = sum(2 if (row.get("type") or "").strip().lower() == "query" else 1 for row in rows)
    counts = {"generated": 0, "judged": 0}
    counts_lock = threading.Lock()

    graded_rows: List[Dict[str, Any]] = []
    judge_pool = ThreadPoolExecutor(max_workers=JUDGE_MAX_WORKERS)
    judge_futures = []

    def _submit_judging(ungraded_row: Dict[str, Any]) -> None:
        with counts_lock:
            counts["generated"] += 1
            if on_progress:
                on_progress(f"Generated {counts['generated']}/{estimated_total}")
        judge_futures.append(judge_pool.submit(_judge_row, ungraded_row, rate_limiter))

    try:
        with ThreadPoolExecutor(max_workers=GENERATION_MAX_WORKERS) as gen_pool:
            gen_futures = [
                gen_pool.submit(run_test_case, base_url, token, version, slno, row, _submit_judging)
                for slno, row in enumerate(rows, start=1)
            ]
            for future in as_completed(gen_futures):
                future.result()  # surface any unexpected exception immediately

        for future in as_completed(judge_futures):
            graded_rows.append(future.result())
            with counts_lock:
                counts["judged"] += 1
                if on_progress:
                    on_progress(f"Scored {counts['judged']}/{estimated_total}")
    finally:
        judge_pool.shutdown(wait=True)

    graded_rows.sort(key=lambda r: (r["slno"], 0 if r["pipeline_path"] == "generation" else 1))
    return graded_rows
