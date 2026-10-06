#!/usr/bin/env sh
# First-time setup chat (Linux/macOS/Git Bash): runs `python -m jobagent telegram --listen` with .env loaded by the app.
# In Telegram: send your resume PDF, then /setup; send /done (or Ctrl+C) when finished.
# Usage: ./scripts/setup_chat.sh [--max-seconds N] [--force]
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env not found - copy .env.example to .env first" >&2; exit 1; }
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
  "$PY" -m pip install --upgrade pip && "$PY" -m pip install -e ".[dev]"
fi
PYTHONIOENCODING=utf-8 exec "$PY" -m jobagent telegram --listen "$@"
