#!/usr/bin/env bash
set -euo pipefail

# Resolve the real script directory even if invoked via a symlink
SOURCE="${BASH_SOURCE[0]}"
while [[ -h "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment in $VENV_DIR" >&2
  python3 -m venv "$VENV_DIR"
  "$PIP" install --upgrade pip >/dev/null
fi

# Ensure dependencies are installed (dotenv and console entrypoints)
if ! "$PY" -c 'import dotenv' >/dev/null 2>&1; then
  echo "Bootstrapping dependencies into $VENV_DIR" >&2
  if [[ -f requirements.txt ]]; then
    "$PIP" install -r requirements.txt >/dev/null || true
  fi
  # Install package in editable mode to get console scripts
  "$PIP" install -e "$SCRIPT_DIR" >/dev/null || true
fi

exec "$PY" cursor_notifier.py "$@"
