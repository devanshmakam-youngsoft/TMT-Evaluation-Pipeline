"""
Config for the eval automation tool - fully standalone, no import of any
dolly-ai-backend code. Real secrets (GROQ_API_KEY, eval login creds) live in
a local .env file next to this one, never committed (see .gitignore).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env")

INCOMING_DIR = _HERE / "incoming"
PROCESSED_DIR = INCOMING_DIR / "processed"
REPORTS_DIR = _HERE / "reports"

# How often to check the incoming/ folder for a new test-case CSV.
POLL_INTERVAL_SECONDS = int(os.getenv("EVAL_POLL_INTERVAL_SECONDS", "10"))

# Login credentials for calling the real (protected) /foundry endpoints -
# set in .env, no hardcoded real values here.
LOGIN_EMAIL = os.getenv("EVAL_LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.getenv("EVAL_LOGIN_PASSWORD", "")

# Judge LLM - Groq, not Azure OpenAI. Plain API key, no Azure SDK/CLI/identity
# dependency at all - the whole point being this runs anywhere, in any
# container, with zero OS-specific credential handling.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_JUDGE_MODEL = os.getenv("GROQ_JUDGE_MODEL", "openai/gpt-oss-20b")
