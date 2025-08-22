#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment in .venv" >&2
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
fi

exec .venv/bin/python cursor_notifier.py "$@"
