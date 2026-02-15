#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: check_html.sh <file>" >&2
  exit 2
fi

if command -v tidy >/dev/null 2>&1; then
  tidy -errors -quiet "${FILE}"
else
  echo "tidy not available; skip" >&2
  exit 0
fi
