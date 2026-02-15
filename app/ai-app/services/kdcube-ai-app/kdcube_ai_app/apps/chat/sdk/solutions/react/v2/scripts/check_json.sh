#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: check_json.sh <file>" >&2
  exit 2
fi

python - <<'PY' "${FILE}"
import json, sys, pathlib
path = pathlib.Path(sys.argv[1])
json.loads(path.read_text(encoding="utf-8"))
print("ok")
PY
