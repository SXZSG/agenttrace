#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-.venv/bin/python}"
PORT="${AGENTTRACE_PORT:-8001}"
if [ ! -x "$PYTHON" ]; then
  echo "Virtual environment not found. Create .venv and install .[dev]." >&2
  exit 1
fi
exec "$PYTHON" -m uvicorn agenttrace.api:app --host 127.0.0.1 --port "$PORT"
