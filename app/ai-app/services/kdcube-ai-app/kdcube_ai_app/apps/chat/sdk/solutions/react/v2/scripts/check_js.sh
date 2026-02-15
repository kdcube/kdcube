#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: check_js.sh <file>" >&2
  exit 2
fi

if command -v node >/dev/null 2>&1; then
  node --check "${FILE}"
else
  echo "node not available; skip" >&2
  exit 0
fi
