import csv
from pathlib import Path
from typing import Any, Dict, List

REPORT_COLUMNS = [
    "slno", "version", "type", "pipeline_path", "question", "expected_answer", "expected_source",
    "generated_answer", "actual_sources", "llm_score", "llm_comments",
    "time_taken_seconds", "error",
]


def write_report(results: List[Dict[str, Any]], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in results:
            writer.writerow({col: row.get(col, "") for col in REPORT_COLUMNS})
