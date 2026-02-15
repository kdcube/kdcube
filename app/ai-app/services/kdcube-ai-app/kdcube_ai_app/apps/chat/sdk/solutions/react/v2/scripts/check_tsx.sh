#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: check_tsx.sh <file>" >&2
  exit 2
fi

if command -v tsc >/dev/null 2>&1; then
  tsc --pretty false --noEmit --jsx react --allowJs "${FILE}"
else
  echo "tsc not available; skip" >&2
  exit 0
fi
