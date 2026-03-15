from __future__ import annotations

import json
import os
import sys
import urllib.request
from urllib.parse import quote

SECRETS_URL = os.getenv("SECRETS_URL", "http://127.0.0.1:7777")
ADMIN_TOKEN = os.getenv("SECRETS_ADMIN_TOKEN")


def _post_set(key: str, value: str) -> None:
    data = json.dumps({"key": key, "value": value}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ADMIN_TOKEN:
        headers["X-KDCUBE-ADMIN-TOKEN"] = ADMIN_TOKEN
    req = urllib.request.Request(
        f"{SECRETS_URL}/set",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Request failed: {resp.status}")


def _delete(key: str) -> None:
    headers = {}
    if ADMIN_TOKEN:
        headers["X-KDCUBE-ADMIN-TOKEN"] = ADMIN_TOKEN
    req = urllib.request.Request(
        f"{SECRETS_URL}/secret/{quote(key, safe='')}",
        headers=headers,
        method="DELETE",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Request failed: {resp.status}")


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "set":
        key, value = sys.argv[2], sys.argv[3]
        _post_set(key, value)
        print("ok")
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "delete":
        _delete(sys.argv[2])
        print("ok")
        return 0
    print("Usage: secretsctl.py set KEY VALUE | delete KEY")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
