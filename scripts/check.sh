#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel)"

cd "$REPO_ROOT"
"$SCRIPT_DIR/check-private-files.sh"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
git diff --check
git diff --cached --check
