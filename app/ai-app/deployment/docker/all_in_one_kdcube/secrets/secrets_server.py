from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

STORE_PATH = os.getenv("SECRETS_STORE_PATH", "/run/kdcube-secrets/store.json")
ADMIN_TOKEN = os.getenv("SECRETS_ADMIN_TOKEN")
READ_TOKENS_RAW = os.getenv("SECRETS_READ_TOKENS", "")
TOKEN_TTL_SECONDS = int(os.getenv("SECRETS_TOKEN_TTL_SECONDS", "600"))
TOKEN_MAX_USES = int(os.getenv("SECRETS_TOKEN_MAX_USES", "1000"))
_token_state: dict[str, dict[str, float]] = {}
_STORE_LOCK = threading.RLock()

app = FastAPI()
logging.basicConfig(
    level=os.getenv("SECRETS_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("kdcube.secrets")


class SecretItem(BaseModel):
    key: str
    value: str
    expected_generation: int | None = None


class SecretStoreUnavailable(RuntimeError):
    pass


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _load_store() -> dict[str, str]:
    path = Path(STORE_PATH)
    if not path.exists():
        return {}
    try:
        if os.name == "posix":
            path.chmod(0o600)
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise SecretStoreUnavailable from exc
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        raise SecretStoreUnavailable
    return payload


def _save_store(data: dict[str, str]) -> None:
    path = Path(STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        descriptor = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.replace(tmp, path)
        if os.name == "posix":
            path.chmod(0o600)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        raise SecretStoreUnavailable from exc


def _read_tokens() -> set[str]:
    tokens: set[str] = set()
    for token in READ_TOKENS_RAW.split(","):
        token = token.strip()
        if token:
            tokens.add(token)
    return tokens


def _inventory_prefix(key: str) -> str | None:
    return key[: -len("__keys")] if key.endswith(".__keys") else None


def _require_admin(token: str | None) -> None:
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="admin token required")


def _require_read_token(token: str | None) -> None:
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
def get_secret(key: str, x_kdcube_secret_token: str | None = Header(default=None)) -> dict[str, str]:
    _require_read_token(x_kdcube_secret_token)
    try:
        with _STORE_LOCK:
            store = _load_store()
            prefix = _inventory_prefix(key)
            if prefix is not None:
                keys = sorted(
                    item
                    for item in store
                    if item.startswith(prefix) and _inventory_prefix(item) is None
                )
                value = json.dumps(keys) if keys else None
            else:
                value = store.get(key)
    except SecretStoreUnavailable:
        raise HTTPException(status_code=503, detail="secret store unavailable") from None
    if value is None:
        logger.info("GET secret ref=%s -> not found", _key_digest(key))
        raise HTTPException(status_code=404, detail="secret not found")
    logger.info("GET secret ref=%s -> ok", _key_digest(key))
    return {"value": value}


@app.post("/set")
def set_secret(item: SecretItem, x_kdcube_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_kdcube_admin_token)
    if _inventory_prefix(item.key) is not None:
        return {"status": "ok", "inventory": "derived"}
    if item.expected_generation not in {None, 0}:
        raise HTTPException(status_code=409, detail="generation conflict")
    try:
        with _STORE_LOCK:
            store = _load_store()
            if item.expected_generation == 0 and item.key in store:
                raise HTTPException(status_code=409, detail="generation conflict")
            store[item.key] = item.value
            _save_store(store)
    except SecretStoreUnavailable:
        raise HTTPException(status_code=503, detail="secret store unavailable") from None
    logger.info("SET secret ref=%s -> ok", _key_digest(item.key))
    response: dict[str, Any] = {"status": "ok"}
    if item.expected_generation == 0:
        response["generation"] = 1
    return response


@app.delete("/secret/{key}")
def delete_secret(key: str, x_kdcube_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_kdcube_admin_token)
    if _inventory_prefix(key) is not None:
        return {"status": "ok", "deleted": False, "inventory": "derived"}
    try:
        with _STORE_LOCK:
            store = _load_store()
            deleted = key in store
            if deleted:
                del store[key]
                _save_store(store)
    except SecretStoreUnavailable:
        raise HTTPException(status_code=503, detail="secret store unavailable") from None
    logger.info(
        "DELETE secret ref=%s -> %s",
        _key_digest(key),
        "ok" if deleted else "not found",
    )
    return {"status": "ok", "deleted": deleted}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("SECRETS_PORT", "7777")))
