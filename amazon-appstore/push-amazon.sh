#!/usr/bin/env bash
# Thin wrapper for the Amazon Appstore push script.
# Unlike the google-play / app-store-connect skills, this one has NO third-party
# deps (the Amazon App Submission API is plain REST + a bearer token), so it just
# runs on system python3 — no venv required.
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SKILL_DIR/push-amazon.py" "$@"
