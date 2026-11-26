#!/usr/bin/env bash
# Thin wrapper — uses the skill's own venv so system Python stays clean.
#
# The script needs Python 3.10+ (it uses `X | None` type syntax). The macOS
# system Python is 3.9, so the venv is created with a newer interpreter when
# one is available. See SKILL.md Gotcha 9.
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SKILL_DIR/.venv"

if [[ ! -x "$VENV/bin/python3" ]]; then
  echo "✗ venv not found. Run once to set it up (Python 3.10+ required):"
  echo "  /opt/homebrew/bin/python3.13 -m venv $VENV"
  echo "  $VENV/bin/pip install PyJWT requests cryptography"
  exit 1
fi

# Fail loudly rather than with a confusing TypeError deep in the script.
if ! "$VENV/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "✗ $VENV is Python $("$VENV/bin/python3" -c 'import sys; print(sys.version.split()[0])')"
  echo "  The script needs Python 3.10+ (it uses X | None type syntax)."
  echo "  Recreate it with a newer interpreter:"
  echo "    rm -rf $VENV"
  echo "    /opt/homebrew/bin/python3.13 -m venv $VENV"
  echo "    $VENV/bin/pip install PyJWT requests cryptography"
  exit 1
fi

exec "$VENV/bin/python3" "$SKILL_DIR/push-metadata.py" "$@"
