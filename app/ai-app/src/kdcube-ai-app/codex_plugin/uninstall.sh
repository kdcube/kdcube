#!/usr/bin/env bash
# Remove the kdcube-builder Codex CLI extension.
set -euo pipefail

DEST="${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}"
PROMPTS_DIR="${CODEX_PROMPTS_DIR:-$HOME/.codex/prompts}"
AGENTS_FILE="${CODEX_AGENTS_FILE:-$HOME/.codex/AGENTS.md}"

BEGIN_MARK="<!-- kdcube-builder:begin -->"
END_MARK="<!-- kdcube-builder:end -->"

if [ -d "$DEST" ]; then
  rm -rf "$DEST"
  echo "removed $DEST"
fi

if [ -d "$PROMPTS_DIR" ]; then
  rm -f "$PROMPTS_DIR"/kdcube-*.md
  echo "removed $PROMPTS_DIR/kdcube-*.md"
fi

if [ -f "$AGENTS_FILE" ]; then
  TMP="$(mktemp)"
  awk -v begin="$BEGIN_MARK" -v end="$END_MARK" '
    $0 == begin { skip=1; next }
    $0 == end   { skip=0; next }
    !skip
  ' "$AGENTS_FILE" > "$TMP"
  mv "$TMP" "$AGENTS_FILE"
  echo "stripped kdcube-builder block from $AGENTS_FILE"
fi

echo "done."