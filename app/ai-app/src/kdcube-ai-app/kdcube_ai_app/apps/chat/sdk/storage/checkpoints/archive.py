"""Copy/readback and exact resolution of newly archived PostgreSQL bytes.

The backend is only asked for the manifest's content-addressed checkpoint-db
key. Existing ConversationStore bodies, attachments and executions are never
listed, read, copied, moved or deleted by this component.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
import json
from typing import Protocol

from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.errors import (
    CheckpointIntegrityError, CheckpointUnavailableError,
)
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.manifest import (
    CreationProvenance, PayloadManifest, TypedPayload,
)
from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.scope import (
    CheckpointScope, PayloadKey, canonical_json,
)


class ByteBackend(Protocol):
    async def exists_a(self, path: str) -> bool: ...
    async def read_bytes_a(self, path: str) -> bytes: ...
    async def write_bytes_a(self, path: str, data: bytes, meta: dict | None = None) -> None: ...


class CheckpointArchive:
    def __init__(self, backend: ByteBackend) -> None:
        self.backend = backend

    async def copy_verified(self, scope: CheckpointScope, key: PayloadKey, payload: TypedPayload,
                            creation: CreationProvenance) -> PayloadManifest:
        manifest = PayloadManifest.for_payload(scope, key, payload, creation)
        envelope = {"format": 1, "scope": asdict(scope), "key": asdict(key),
                    "serialization_type": payload.serialization_type,
                    "data": None if payload.data is None else base64.b64encode(payload.data).decode("ascii")}
        try:
            if not await self.backend.exists_a(manifest.cold_key):
                await self.backend.write_bytes_a(manifest.cold_key, canonical_json(envelope))
        except Exception as exc:
            raise CheckpointUnavailableError("checkpoint archive write unavailable") from exc
        # Even an idempotent retry independently reads the committed object.
        await self.resolve_checkpoint_payload(scope, key, manifest)
        return manifest

    async def resolve_checkpoint_payload(self, scope: CheckpointScope, key: PayloadKey,
                                         manifest: PayloadManifest) -> TypedPayload:
        manifest.check_binding(scope, key)
        try:
            raw = await self.backend.read_bytes_a(manifest.cold_key)
        except Exception as exc:
            raise CheckpointUnavailableError("exact checkpoint archive payload unavailable") from exc
        # Enforce an envelope budget before parsing/decoding. Identity headers
        # are deterministic; extra data cannot force an unbounded decode.
        overhead = len(canonical_json({"scope": asdict(scope), "key": asdict(key)})) + 1024
        if not isinstance(raw, bytes) or len(raw) > 4 * ((manifest.byte_length + 2) // 3) + overhead:
            raise CheckpointIntegrityError("invalid checkpoint archive envelope length")
        try:
            envelope = json.loads(raw)
            if set(envelope) != {"format", "scope", "key", "serialization_type", "data"}:
                raise ValueError("invalid fields")
            if envelope["format"] != 1 or envelope["scope"] != asdict(scope) or envelope["key"] != asdict(key):
                raise ValueError("invalid binding")
            data = envelope["data"]
            decoded = None if data is None else base64.b64decode(data, validate=True)
            payload = TypedPayload(envelope["serialization_type"], decoded)
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            raise CheckpointIntegrityError("invalid checkpoint archive envelope") from exc
        manifest.check_payload(payload)
        return payload
