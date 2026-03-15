from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

STORE_PATH = os.getenv("SECRETS_STORE_PATH", "/run/kdcube-secrets/store.json")
ADMIN_TOKEN = os.getenv("SECRETS_ADMIN_TOKEN")
READ_TOKENS_RAW = os.getenv("SECRETS_READ_TOKENS", "")
TOKEN_TTL_SECONDS = int(os.getenv("SECRETS_TOKEN_TTL_SECONDS", "600"))
TOKEN_MAX_USES = int(os.getenv("SECRETS_TOKEN_MAX_USES", "1000"))
_token_state: Dict[str, Dict[str, float]] = {}

app = FastAPI()
logging.basicConfig(
    level=os.getenv("SECRETS_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("kdcube.secrets")


class SecretItem(BaseModel):
    key: str
    value: str


def _load_store() -> dict[str, str]:
    path = Path(STORE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_store(data: dict[str, str]) -> None:
    path = Path(STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _read_tokens() -> Set[str]:
    tokens: Set[str] = set()
    for token in READ_TOKENS_RAW.split(","):
        token = token.strip()
        if token:
            tokens.add(token)
    return tokens


def _require_admin(token: Optional[str]) -> None:
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="admin token required")


def _require_read_token(token: Optional[str]) -> None:
    tokens = _read_tokens()
    if tokens and (token not in tokens):
        raise HTTPException(status_code=403, detail="read token required")
    if not token:
        return
    now = time.monotonic()
    state = _token_state.setdefault(token, {"first_seen": now, "uses": 0.0})
    ttl = max(0, TOKEN_TTL_SECONDS)
    max_uses = max(0, TOKEN_MAX_USES)
    if ttl and (now - state["first_seen"] > ttl):
        raise HTTPException(status_code=403, detail="token expired")
    if max_uses and state["uses"] >= max_uses:
        raise HTTPException(status_code=403, detail="token exhausted")
    state["uses"] += 1.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/secret/{key}")
def get_secret(key: str, x_kdcube_secret_token: Optional[str] = Header(default=None)) -> dict[str, str]:
    _require_read_token(x_kdcube_secret_token)
    store = _load_store()
    if key not in store:
        logger.info("GET secret %s -> not found", key)
        raise HTTPException(status_code=404, detail="secret not found")
    logger.info("GET secret %s -> ok", key)
    return {"value": store[key]}


@app.post("/set")
def set_secret(item: SecretItem, x_kdcube_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_kdcube_admin_token)
    store = _load_store()
    store[item.key] = item.value
    _save_store(store)
    logger.info("SET secret %s -> ok", item.key)
    return {"status": "ok"}


@app.delete("/secret/{key}")
def delete_secret(key: str, x_kdcube_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_kdcube_admin_token)
    store = _load_store()
    deleted = key in store
    if deleted:
        del store[key]
        _save_store(store)
    logger.info("DELETE secret %s -> %s", key, "ok" if deleted else "not found")
    return {"status": "ok", "deleted": deleted}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("SECRETS_PORT", "7777")))
