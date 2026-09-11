import csv
from pathlib import Path
from typing import Any, Dict, List

# One column per graph node's "node_metrics" key (dolly-ai-backend's
# GenerationState) - keep this in sync with every node.py that sets
# node_metrics, since a mismatched name here just shows up blank, not an error.
NODE_METRIC_COLUMNS = [
    "get_history", "query_analyzer", "retriever-cache-hit", "retriever-cache-miss",
    "cache_follow_up", "get_sources_from_chunks", "resolve_links", "answer_generator",
    "conversational_responder", "conversational_follow_up", "follow_up",
]

REPORT_COLUMNS = [
    "slno", "version", "type", "pipeline_path", "question", "expected_answer", "expected_source",
    "generated_answer", "actual_sources", *NODE_METRIC_COLUMNS, "llm_score", "llm_comments",
    "time_taken_seconds", "error",
]


def write_report(results: List[Dict[str, Any]], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in results:
            writer.writerow({col: row.get(col, "") for col in REPORT_COLUMNS})
