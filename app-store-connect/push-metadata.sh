#!/usr/bin/env bash
# Thin wrapper — uses the skill's own venv so system Python stays clean.
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SKILL_DIR/.venv"

if [[ ! -x "$VENV/bin/python3" ]]; then
  echo "✗ venv not found. Run once to set it up:"
  echo "  python3 -m venv $VENV"
  echo "  $VENV/bin/pip install PyJWT requests cryptography"
  exit 1
fi

exec "$VENV/bin/python3" "$SKILL_DIR/push-metadata.py" "$@"
