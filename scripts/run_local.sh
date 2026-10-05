#!/usr/bin/env sh
# Local runner (Linux/macOS/Git Bash). Usage:
#   ./scripts/run_local.sh [mode]       # default: full (uses .env; DRY_RUN default true)
#   FAKE=1 ./scripts/run_local.sh       # offline demo: fake jobs + fake LLM + sqlite + dry run
#   SETUP=1 ./scripts/run_local.sh      # (re)install dependencies + Chromium first
set -eu
cd "$(dirname "$0")/.."
MODE="${1:-full}"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
  SETUP=1
fi
if [ "${SETUP:-}" = "1" ]; then
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -e ".[dev]"
  "$PY" -m playwright install chromium
fi
if [ "${FAKE:-}" = "1" ]; then export FAKE_MODE=true DRY_RUN=true DB_BACKEND=sqlite; fi
PYTHONIOENCODING=utf-8 exec "$PY" -m jobagent run --mode "$MODE"
