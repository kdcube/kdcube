#!/usr/bin/env bash
# Install the kdcube-builder Codex CLI extension.
# Idempotent — rerun to update.
set -euo pipefail

HERE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILDER="$HERE/../builder_plugin/plugins/kdcube-builder"

if [ ! -d "$BUILDER" ]; then
  echo "error: cannot find sibling builder_plugin at $BUILDER" >&2
  echo "This installer expects codex_plugin/ to live next to builder_plugin/ in the kdcube-ai-app repo." >&2
  exit 1
fi

DEST="${KDCUBE_BUILDER_ROOT:-$HOME/.codex/kdcube-builder}"
PROMPTS_DIR="${CODEX_PROMPTS_DIR:-$HOME/.codex/prompts}"
AGENTS_FILE="${CODEX_AGENTS_FILE:-$HOME/.codex/AGENTS.md}"

BEGIN_MARK="<!-- kdcube-builder:begin -->"
END_MARK="<!-- kdcube-builder:end -->"

mkdir -p "$DEST" "$DEST/templates" "$PROMPTS_DIR" "$(dirname "$AGENTS_FILE")"

# 1. Runtime + templates (copied from builder_plugin).
cp "$BUILDER/scripts/kdcube_local.py" "$DEST/kdcube_local.py"
chmod +x "$DEST/kdcube_local.py" 2>/dev/null || true
for f in "$BUILDER/templates/"*.yaml; do
  cp "$f" "$DEST/templates/"
done

# 2. Prompt files.
for f in "$HERE"/prompts/*.md; do
  cp "$f" "$PROMPTS_DIR/"
done

# 3. Merge AGENTS.md block.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if [ -f "$AGENTS_FILE" ]; then
  # Strip any previous kdcube-builder block.
  awk -v begin="$BEGIN_MARK" -v end="$END_MARK" '
    $0 == begin { skip=1; next }
    $0 == end   { skip=0; next }
    !skip
  ' "$AGENTS_FILE" > "$TMP"
  mv "$TMP" "$AGENTS_FILE"
  TMP="$(mktemp)"
fi

{
  [ -s "$AGENTS_FILE" ] && printf '\n'
  printf '%s\n' "$BEGIN_MARK"
  cat "$HERE/AGENTS.md"
  printf '%s\n' "$END_MARK"
} >> "$AGENTS_FILE"

cat <<EOF
Installed kdcube-builder for Codex CLI:
  runtime:  $DEST
  prompts:  $PROMPTS_DIR/kdcube-*.md
  agents:   $AGENTS_FILE (block between $BEGIN_MARK / $END_MARK)

Next steps:
  1. Ensure 'kdcube' is in PATH:  pip install --user kdcube-cli
  2. (optional) Add a Playwright MCP server to ~/.codex/config.toml for /kdcube-ui-test.
  3. Launch Codex:  codex
EOF