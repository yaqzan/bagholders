#!/usr/bin/env bash
# Install Trader git hooks. Run once after a fresh clone.
#
# Usage: bash tools/git-hooks/install.sh
set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

HOOK_SRC="tools/git-hooks/pre-commit"
HOOK_DST=".git/hooks/pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
  echo "install.sh: $HOOK_SRC not found. Are you in the repo root?"
  exit 1
fi

cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"

echo "Installed pre-commit hook → $HOOK_DST"
echo "Test it: python tests/test_strategy_config_drift.py && python tests/test_mechanism_registry.py"
