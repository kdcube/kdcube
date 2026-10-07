# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
"""Verified cold copies of ConversationStore message bodies (W536/W619).

Layout, in the store's configured backend (file or S3):

    cb/tenants/{t}/projects/{p}/conversation/{user}/{conv}/{turn}/{message_id}.json        hot
    cb/tenants/{t}/projects/{p}/conversation-cold-bodies/{yyyy}/{mm}/{dd}/{user}/{conv}/{turn}/{message_id}.json   cold

The day is the message's creation day, read from its id
(`{role}-YYYY-MM-DDTHH-MM-SS-{hex}`), so the cold key of any message is known
from its hot key alone: reads and deletes reach the cold copy by date even
when the hot key is gone.

A move copies the body to the cold key, reads it back and compares its
sha256, and only then replaces the hot object with a small pointer (the
minimal metadata: ids, role, timestamp, cold key, sha256). File storage
replaces atomically (os.replace); an object-store PUT is atomic. A failure
at any step before the pointer leaves the hot body in place.

Reads return the body with `storage` set to "hot", "cold" or "unavailable"
(cold copy missing, corrupt or failing its hash), never an empty body.

Not moved by this pass, and why: the nightly archive moves the bodies its
archived index rows reference. A `hosted_uri` that is not a message body
("index_only", or a key outside `conversation/`) is skipped with its count
(`bodies_not_a_body`) and its index row still moves. Blobs under
`attachments/` and `executions/` (and message bodies no index row references)
are not moved: they are binary or tree-shaped, read through `get_blob_bytes`
and bundle paths that have no cold fallback yet, and whether they move is
pending Root's decision (W619 scope note).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tempfile
from typing import Any, Optional

POINTER_SCHEMA = "kdcube.conversation-cold-body-pointer.v1"
HOT_SEGMENT = "/conversation/"
COLD_SEGMENT = "/conversation-cold-bodies/"
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})T\d{2}-\d{2}-\d{2}")

# Outcomes of `move_async`.
MOVED, ALREADY_COLD, NOT_A_BODY, MISSING = "moved", "already_cold", "not_a_body", "missing"


def is_body_key(rel: Any) -> bool:
    """A ConversationStore message body key (not "index_only", attachments or executions)."""

    return isinstance(rel, str) and HOT_SEGMENT in rel and rel.endswith(".json") and COLD_SEGMENT not in rel


def body_day(rel: str) -> Optional[tuple]:
    m = _DAY.search(rel.rsplit("/", 1)[-1])
    return (m.group(1), m.group(2), m.group(3)) if m else None


def cold_key_for(rel: str) -> str:
    if not is_body_key(rel):
        raise ValueError(f"not a conversation message key: {rel}")
    head, rest = rel.split(HOT_SEGMENT, 1)
    day = body_day(rel)
    dated = "/".join(day) if day else "undated"
    return f"{head}{COLD_SEGMENT}{dated}/{rest}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _decode(pointer: dict, data: bytes, *, verify: bool = True) -> dict:
    if verify and _sha(data) != pointer.get("sha256"):
        return _unavailable(pointer, "cold_body_hash_mismatch")
    try:
        body = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unavailable(pointer, "cold_body_invalid_json")
    if not isinstance(body, dict):
        return _unavailable(pointer, "cold_body_invalid_json")
    body["storage"] = "cold"
    return body


def _hot(obj: Any) -> Any:
    if isinstance(obj, dict):
        obj["storage"] = "hot"
    return obj


def _orphan_pointer(rel: str) -> dict:
    """What is known of a message whose hot key is gone: its key."""

    parts = rel.strip("/").split("/")
    return {"message_id": parts[-1][:-len(".json")], "turn_id": parts[-2] if len(parts) > 1 else None}


def read_sync(backend: Any, rel: str, raw: str) -> dict:
    obj = json.loads(raw)
    if not is_body_key(rel) or not _pointer(obj, rel):
        return _hot(obj)
    try:
        return _decode(obj, backend.read_bytes(obj["cold_key"]))
    except Exception as exc:
        return _unavailable(obj, type(exc).__name__)


async def read_async(backend: Any, rel: str, raw: Optional[bytes]) -> dict:
    """Decode a hot object (`raw`), or, with `raw` None (hot key absent), the cold copy by date.

    Raises FileNotFoundError when neither tier has the message.
    """

    if raw is None:
        if not is_body_key(rel):
            raise FileNotFoundError(rel)
        cold_key = cold_key_for(rel)
        if not await backend.exists_a(cold_key):
            raise FileNotFoundError(rel)
        # No pointer, so no recorded hash: the body is served as cold when it parses.
        return _decode(_orphan_pointer(rel), await backend.read_bytes_a(cold_key), verify=False)
    obj = json.loads(raw)
    if not is_body_key(rel) or not _pointer(obj, rel):
        return _hot(obj)
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


async def move_async(backend: Any, rel: str) -> str:
    """Move one body to its dated cold key; returns MOVED, ALREADY_COLD, NOT_A_BODY or MISSING.

    Raises (leaving the hot body in place) when the cold copy does not read
    back with the same sha256, or the hot body changed during the copy.
    """

    if not is_body_key(rel):
        return NOT_A_BODY
    cold_key = cold_key_for(rel)
    if not await backend.exists_a(rel):
        if await backend.exists_a(cold_key):
            return ALREADY_COLD
        return MISSING
    raw = await backend.read_bytes_a(rel)
    obj = json.loads(raw)
    if _pointer(obj, rel):
        if _sha(await backend.read_bytes_a(cold_key)) != obj.get("sha256"):
            raise ValueError(f"cold body hash mismatch at {cold_key}")
        return ALREADY_COLD
    if not isinstance(obj, dict):
        raise ValueError(f"message object is not a JSON object: {rel}")
    digest = _sha(raw)
    await backend.write_bytes_a(cold_key, raw, meta={"ContentType": "application/json"})
    if _sha(await backend.read_bytes_a(cold_key)) != digest:
        try:  # leave no unverified copy behind; the hot body is untouched
            await backend.delete_a(cold_key)
        except Exception:
            pass
        raise ValueError(f"cold body readback mismatch at {cold_key}")
    if await backend.read_bytes_a(rel) != raw:
        raise ValueError(f"message changed during cold copy: {rel}")
    meta = obj.get("meta") or {}
    pointer = {
        "schema": POINTER_SCHEMA, "cold_key": cold_key, "sha256": digest, "bytes": len(raw),
        "tenant": obj.get("tenant"), "project": obj.get("project"),
        "user_id": obj.get("user_id"), "conversation_id": obj.get("conversation_id"),
        "turn_id": obj.get("turn_id"), "role": obj.get("role"),
        "timestamp": obj.get("timestamp"), "message_id": meta.get("message_id"),
    }
    await _write_pointer(backend, rel, json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode())
    if not _pointer(json.loads(await backend.read_bytes_a(rel)), rel):
        raise ValueError(f"cold body pointer readback mismatch at {rel}")
    return MOVED


async def delete_async(backend: Any, rel: str) -> bool:
    """Delete a message in both tiers; False when neither had it."""

    deleted = False
    if is_body_key(rel):
        cold_key = cold_key_for(rel)
        if await backend.exists_a(cold_key):
            await backend.delete_a(cold_key)
            deleted = True
    if await backend.exists_a(rel):
        await backend.delete_a(rel)
        deleted = True
    return deleted


async def restore_async(backend: Any, rel: str) -> bool:
    obj = json.loads(await backend.read_bytes_a(rel))
    if not _pointer(obj, rel):
        return False
    body = await backend.read_bytes_a(obj["cold_key"])
    if _sha(body) != obj.get("sha256"):
        raise ValueError(f"cold body hash mismatch at {obj['cold_key']}")
    await _write_pointer(backend, rel, body)
    if await backend.read_bytes_a(rel) != body:
        raise ValueError(f"body restore readback mismatch at {rel}")
    await backend.delete_a(obj["cold_key"])
    return True
