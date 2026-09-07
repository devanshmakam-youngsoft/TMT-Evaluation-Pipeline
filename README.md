# Eval automation

Lives outside `dolly-ai-backend/` entirely - own folder at the project root, own git history (or
none), no import dependency on that repo. Safe to ignore/delete without affecting anything.
Deliberately separate from any future tracing/debugging tool - this one just does: question in,
answer out, score it.

## Usage

Run from the project root (`D:\company-projects\TMT-offical\`):

```bash
python -m eval_automation.watch_and_run
```

Asks once whether to test against local or the deployed backend, logs in, then keeps running -
drop a test-case CSV into `incoming/` any time and it picks it up automatically (checks every
10s), no need to restart the process per run.

## Test-case CSV format (input)

| Column | Required? | Meaning |
|---|---|---|
| `version` | required | Free-text label for this batch - convention: use the file's own name |
| `type` | optional | e.g. `conversation` or `query` - your own classification, not enforced |
| `pipeline_path` | optional | `generation` (default) hits `/foundry/generate` only. `regeneration` also calls `/foundry/retry` right after, on the same question - produces 2 output rows for that question |
| `question` | required | Sent verbatim to the real chatbot |
| `expected_answer` | required | What the answer should mean (wording can differ) |
| `expected_source` | optional | Filename you expect it to cite |
| `remarks` | optional | Extra grading context for the judge, e.g. "accept a shorter answer than expected" |

See `incoming/sample_test_cases.csv.example` for the format (rename to `.csv` and drop it in
`incoming/` to try it).

## Report (output)

Written to `reports/<filename>_report_<timestamp>.csv`, one row per call
(`pipeline_path=regeneration` produces 2 rows per input row - one `call_type=generation`, one
`call_type=regeneration`):

| Column | Meaning |
|---|---|
| `version`, `type` | Echoed from input |
| `call_type` | `generation` or `regeneration` - which call this row is |
| `question`, `expected_answer`, `expected_source` | Echoed from input |
| `generated_answer` | The real answer from that call |
| `actual_sources` | Real filenames actually cited, comma-separated |
| `llm_score` | 0-10 - a judge's holistic score: does the answer satisfy the question, match the expected answer's meaning, cite the right source |
| `llm_comments` | What's missing or wrong, or why it's correct, in 1-2 sentences |
| `time_taken_seconds` | How long that specific call took |
| `error` | Only set if the call itself failed - empty on a normal run |

The processed input CSV is moved to `incoming/processed/` so it doesn't get re-run.
