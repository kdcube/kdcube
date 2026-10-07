# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
"""Verified cold copies of ConversationStore message objects.

The original message key remains a small pointer so existing URIs and
directory-based conversation reads continue to work. The body is copied to
the configured store's cold prefix and read back by hash before that pointer
replaces the hot object. Repeating a move verifies the existing cold copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any

POINTER_SCHEMA = "kdcube.conversation-cold-body-pointer.v1"


def cold_key_for(rel: str) -> str:
    if "/conversation/" not in rel or not rel.endswith(".json"):
        raise ValueError(f"not a conversation message key: {rel}")
    return rel.replace("/conversation/", "/conversation-cold-bodies/", 1)


def _pointer(obj: Any, rel: str) -> bool:
    return isinstance(obj, dict) and obj.get("schema") == POINTER_SCHEMA and obj.get("cold_key") == cold_key_for(rel)


def _unavailable(pointer: dict, reason: str) -> dict:
    return {
        "tenant": pointer.get("tenant"), "project": pointer.get("project"),
        "user_id": pointer.get("user_id"), "conversation_id": pointer.get("conversation_id"),
        "turn_id": pointer.get("turn_id"), "role": pointer.get("role"),
        "timestamp": pointer.get("timestamp"), "text": None, "payload": None,
        "meta": {"message_id": pointer.get("message_id")},
        "storage": "unavailable", "body_unavailable_reason": reason,
    }


def _decode(pointer: dict, data: bytes) -> dict:
    if hashlib.sha256(data).hexdigest() != pointer.get("sha256"):
        return _unavailable(pointer, "cold_body_hash_mismatch")
    try:
        body = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unavailable(pointer, "cold_body_invalid_json")
    if not isinstance(body, dict):
        return _unavailable(pointer, "cold_body_invalid_json")
    body["storage"] = "cold"
    return body


def read_sync(backend: Any, rel: str, raw: str) -> dict:
    obj = json.loads(raw)
    if not _pointer(obj, rel):
        return obj
    try:
        return _decode(obj, backend.read_bytes(obj["cold_key"]))
    except Exception as exc:
        return _unavailable(obj, type(exc).__name__)


async def read_async(backend: Any, rel: str, raw: bytes) -> dict:
    obj = json.loads(raw)
    if not _pointer(obj, rel):
        return obj
    try:
        return _decode(obj, await backend.read_bytes_a(obj["cold_key"]))
    except Exception as exc:
        return _unavailable(obj, type(exc).__name__)


async def _write_pointer(backend: Any, rel: str, data: bytes) -> None:
    """Use atomic replacement on file storage; object-store PUTs are atomic."""

    base = getattr(backend, "base_path", None)
    if base is None:
        await backend.write_bytes_a(rel, data, meta={"ContentType": "application/json"})
        return
    root = pathlib.Path(base).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError("message key leaves configured storage root")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".body-pointer-", delete=False) as file:
            temporary = pathlib.Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def move_async(backend: Any, rel: str) -> bool:
    """Return True after a new verified move, False for an existing pointer."""

    cold_key = cold_key_for(rel)
    raw = await backend.read_bytes_a(rel)
    obj = json.loads(raw)
    if _pointer(obj, rel):
        cold = await backend.read_bytes_a(cold_key)
        if hashlib.sha256(cold).hexdigest() != obj.get("sha256"):
            raise ValueError(f"cold body hash mismatch at {cold_key}")
        return False
    if not isinstance(obj, dict):
        raise ValueError(f"message object is not a JSON object: {rel}")
    digest = hashlib.sha256(raw).hexdigest()
    await backend.write_bytes_a(cold_key, raw, meta={"ContentType": "application/json"})
    readback = await backend.read_bytes_a(cold_key)
    if hashlib.sha256(readback).hexdigest() != digest or readback != raw:
        raise ValueError(f"cold body readback mismatch at {cold_key}")
    if await backend.read_bytes_a(rel) != raw:
        raise ValueError(f"message changed during cold copy: {rel}")
    meta = obj.get("meta") or {}
    pointer = {
        "schema": POINTER_SCHEMA, "cold_key": cold_key, "sha256": digest,
        "tenant": obj.get("tenant"), "project": obj.get("project"),
        "user_id": obj.get("user_id"), "conversation_id": obj.get("conversation_id"),
        "turn_id": obj.get("turn_id"), "role": obj.get("role"),
        "timestamp": obj.get("timestamp"), "message_id": meta.get("message_id"),
    }
    await _write_pointer(backend, rel, json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode())
    if not _pointer(json.loads(await backend.read_bytes_a(rel)), rel):
        raise ValueError(f"cold body pointer readback mismatch at {rel}")
    return True


async def delete_async(backend: Any, rel: str) -> bool:
    if not await backend.exists_a(rel):
        return False
    obj = json.loads(await backend.read_bytes_a(rel))
    if _pointer(obj, rel) and await backend.exists_a(obj["cold_key"]):
        await backend.delete_a(obj["cold_key"])
    await backend.delete_a(rel)
    return True


async def restore_async(backend: Any, rel: str) -> bool:
    obj = json.loads(await backend.read_bytes_a(rel))
    if not _pointer(obj, rel):
        return False
    body = await backend.read_bytes_a(obj["cold_key"])
    if hashlib.sha256(body).hexdigest() != obj.get("sha256"):
        raise ValueError(f"cold body hash mismatch at {obj['cold_key']}")
    await _write_pointer(backend, rel, body)
    if await backend.read_bytes_a(rel) != body:
        raise ValueError(f"body restore readback mismatch at {rel}")
    await backend.delete_a(obj["cold_key"])
    return True
