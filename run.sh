#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r backend/requirements.txt

exec .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 "$@"
