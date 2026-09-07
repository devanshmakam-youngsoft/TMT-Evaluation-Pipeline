"""
Eval automation - always-on watcher, not a run-once script.

Drop a test-case CSV (columns: question, expected_answer, expected_source
[optional], remarks [optional]) into eval_automation/incoming/. This process
picks it up automatically, runs every row against the real backend (local or
deployed - asked once at startup), scores each answer, and writes a report
CSV into eval_automation/reports/. The processed input file is moved into
incoming/processed/ so it isn't re-run on the next poll.

Lives outside dolly-ai-backend entirely - own folder, own git history (or
none), not touched by anything in that repo.

Usage (from D:\\company-projects\\TMT-offical\\, this file's parent's parent):
    python -m eval_automation.watch_and_run
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .auth_client import login
from .config import INCOMING_DIR, PROCESSED_DIR, POLL_INTERVAL_SECONDS, REPORTS_DIR
from .report_writer import write_report
from .runner import run_test_cases

_LOCAL_DEFAULT = "http://localhost:8014"
_DEPLOYED_DEFAULT = "https://ca-tmt-dolly-backend-dev.politemeadow-bb00a646.centralus.azurecontainerapps.io/api"


def _pick_base_url() -> str:
    # EVAL_BASE_URL set (e.g. running in a detached Docker container, no
    # terminal to type into) - skip the interactive prompt entirely.
    env_choice = os.getenv("EVAL_BASE_URL", "").strip()
    if env_choice:
        if env_choice == "1":
            return _LOCAL_DEFAULT
        if env_choice == "2":
            return _DEPLOYED_DEFAULT
        return env_choice.rstrip("/")

    print("Test against:")
    print(f"  1) local   ({_LOCAL_DEFAULT})")
    print(f"  2) deployed ({_DEPLOYED_DEFAULT})")
    choice = input("Choice [1/2], or paste a custom base URL: ").strip()
    if choice == "1" or choice == "":
        return _LOCAL_DEFAULT
    if choice == "2":
        return _DEPLOYED_DEFAULT
    return choice.rstrip("/")


def _process_csv(csv_path: Path, base_url: str, token: str) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"[{csv_path.name}] running {len(rows)} test case(s)...")
    results = run_test_cases(base_url, token, rows)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = REPORTS_DIR / f"{csv_path.stem}_report_{timestamp}.csv"
    write_report(results, report_path)
    print(f"[{csv_path.name}] done - report: {report_path}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    csv_path.rename(PROCESSED_DIR / csv_path.name)


def main() -> None:
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    base_url = _pick_base_url()
    print(f"Logging in to {base_url} ...")
    token = login(base_url)
    print("Logged in. Watching for test-case CSVs in "
          f"{INCOMING_DIR} (checking every {POLL_INTERVAL_SECONDS}s). Ctrl+C to stop.")

    try:
        while True:
            for csv_path in sorted(INCOMING_DIR.glob("*.csv")):
                try:
                    _process_csv(csv_path, base_url, token)
                except Exception as exc:
                    print(f"[{csv_path.name}] FAILED: {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
