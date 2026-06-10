#!/usr/bin/env bash
# Launch grove. Ensures deps are synced, then starts the TUI.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install: https://docs.astral.sh/uv/" >&2
  exit 1
fi

uv sync --quiet
exec uv run grove "$@"
