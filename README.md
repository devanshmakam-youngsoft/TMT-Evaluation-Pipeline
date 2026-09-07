# Eval automation

Standalone tool for evaluating the Dolly chatbot - no import dependency on `dolly-ai-backend`,
own `requirements.txt`, own `.env`.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file next to this README (never committed - see `.gitignore`):

```
GROQ_API_KEY=<your key>
GROQ_JUDGE_MODEL=openai/gpt-oss-20b

EVAL_LOGIN_EMAIL=<eval user email>
EVAL_LOGIN_PASSWORD=<eval user password>
```

## Usage

Run from this folder's parent directory:

```bash
python -m eval_automation.gui_run
```

A window opens: pick **Local** or **Deployed**, type a **Version** label (defaults to a
timestamp), click **Run**. It logs in, runs every row of `master.csv` against the real backend,
scores each answer with a Groq-hosted judge, and writes a report to `reports/`. The judge client
is warmed up once when the window opens, so the first test case of the first run doesn't pay a
cold-start connection cost.

**Settings...** lets you change which file is used as the master test set, without editing code -
saved to `settings.json` (not committed) so it's remembered next time.

## Master test file (`master.csv`)

Not committed to git by default (real business content) - keep your own copy locally.

| Column | Required? | Meaning |
|---|---|---|
| `type` | optional | `query` or `conversation` only |
| `pipeline_path` | optional | `generation` (default) hits `/foundry/generate` only. `regeneration` also calls `/foundry/retry` right after, on the same question - produces 2 output rows for that question |
| `question` | required | Sent verbatim to the real chatbot |
| `expected_answer` | required | What the answer should mean (wording can differ) |
| `expected_source` | optional | Filename you expect it to cite |
| `remarks` | optional | Extra grading context for the judge, e.g. "accept a shorter answer than expected" |

`version` isn't a column - you type it once in the GUI per run, and it's stamped onto every
output row for that run.

## Report (output)

Written to `reports/<version>_report_<timestamp>.csv`, one row per call (`pipeline_path=regeneration`
produces 2 rows per input row - one `call_type=generation`, one `call_type=regeneration`):

| Column | Meaning |
|---|---|
| `version`, `type` | Version you typed in the GUI; `type` echoed from input |
| `call_type` | `generation` or `regeneration` - which call this row is |
| `question`, `expected_answer`, `expected_source` | Echoed from input |
| `generated_answer` | The real answer from that call |
| `actual_sources` | Real filenames actually cited, comma-separated |
| `llm_score` | 0-10 - a judge's holistic score: does the answer satisfy the question, match the expected answer's meaning, cite the right source |
| `llm_comments` | What's missing or wrong, or why it's correct, in 1-2 sentences |
| `time_taken_seconds` | How long that specific call took |
| `error` | Only set if the call itself failed - empty on a normal run |
