#!/usr/bin/env bash
# Thin wrapper — runs push-play.py in the skill's own venv (system Python stays clean).
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SKILL_DIR/.venv"

if [[ ! -x "$VENV/bin/python3" ]]; then
  echo "✗ venv not found. Set it up once:"
  echo "  python3 -m venv $VENV"
  echo "  $VENV/bin/pip install google-api-python-client google-auth"
  exit 1
fi

exec "$VENV/bin/python3" "$SKILL_DIR/push-play.py" "$@"
