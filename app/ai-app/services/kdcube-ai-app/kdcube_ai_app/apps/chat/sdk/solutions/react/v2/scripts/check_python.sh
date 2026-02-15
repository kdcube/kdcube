#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: check_python.sh <file>" >&2
  exit 2
fi

python -m py_compile "${FILE}"
