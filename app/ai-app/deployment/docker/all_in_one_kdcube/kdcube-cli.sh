#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/elenaviter/kdcube-ai-app.git"
TARGET_DIR="${KDCUBE_APPS_PATH:-$HOME/.kdcube/kdcube-ai-app}"

mkdir -p "$(dirname "$TARGET_DIR")"

if [ -d "$TARGET_DIR/.git" ]; then
  echo "Using existing repo at $TARGET_DIR"
  if command -v git >/dev/null 2>&1; then
    git -C "$TARGET_DIR" pull
  fi
else
  if ! command -v git >/dev/null 2>&1; then
    echo "Git is required. Please install Git and retry." >&2
    exit 1
  fi
  git clone "$REPO_URL" "$TARGET_DIR"
fi

python3 "$TARGET_DIR/app/ai-app/deployment/docker/all_in_one/kdcube-cli.py"
