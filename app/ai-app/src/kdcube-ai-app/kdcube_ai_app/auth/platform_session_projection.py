# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis projection over the durable platform-session authority."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Mapping, Protocol

from kdcube_ai_app.auth.session_projection_support import (
    decode_session_projection,
    session_projection_generation_scope,
)
from kdcube_ai_app.auth.platform_session_store import (
    PlatformSessionAuthorityFence,
    PlatformSessionAuthorityState,
    PlatformSessionStore,
    PlatformSessionStoreResult,
    _merge_record,
)
from kdcube_ai_app.infra.namespaces import ns_key

logger = logging.getLogger(__name__)

_SCHEMA = "kdcube.platform-session-projection.v1"


class PlatformSessionFenceStore(PlatformSessionStore, Protocol):
    async def get_session_state_by_id(
        self,
        session_id: str,
    ) -> PlatformSessionAuthorityState | None: ...

    async def get_session_state_by_user_id(
        self,
        user_id: str,
    ) -> PlatformSessionAuthorityState | None: ...

    async def get_session_fence(
        self,
        session_id: str,
    ) -> PlatformSessionAuthorityFence | None: ...


class RedisPlatformSessionProjection:
    """Generation-scoped platform-session records with native Redis TTL."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        generation_id: str,
    ) -> None:
        if redis is None:
            raise ValueError("platform session projection requires redis")
        self._redis = redis
        generation = session_projection_generation_scope(generation_id)
        self._prefix = ns_key(
            f"kdcube:auth:platform-session:projection:{generation}",
            tenant=tenant,
            project=project,
        )

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _authority_key(self, authority_key: str) -> str:
        digest = hashlib.sha256(authority_key.encode("utf-8")).hexdigest()
        return f"{self._prefix}:authority:{digest}"

    async def _read_session(
        self,
        session_id: str,
    ) -> PlatformSessionAuthorityState | None:
        key = self._session_key(session_id)
        try:
            payload = decode_session_projection(await self._redis.get(key))
            if not payload or payload.get("schema") != _SCHEMA:
                if payload is not None:
                    await self._redis.delete(key)
                return None
            return PlatformSessionAuthorityState(
                record=dict(payload.get("record") or {}),
                authority_key=str(payload.get("authority_key") or ""),
                revision=int(payload.get("revision") or 0),
                expires_at=float(payload.get("expires_at") or 0),
            )
        except Exception:
            logger.warning("Platform session projection read failed", exc_info=True)
            return None

    async def read_by_session(
        self,
        session_id: str,
    ) -> PlatformSessionAuthorityState | None:
        return await self._read_session(session_id)

    async def read_by_authority(
        self,
        authority_key: str,
    ) -> PlatformSessionAuthorityState | None:
        key = self._authority_key(authority_key)
        try:
            raw = await self._redis.get(key)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            session_id = str(raw or "").strip()
        except Exception:
            logger.warning("Platform session projection index read failed", exc_info=True)
            return None
        return await self._read_session(session_id) if session_id else None

    async def write(
        self,
        state: PlatformSessionAuthorityState,
        *,
        now: float | None = None,
    ) -> None:
        current = float(time.time() if now is None else now)
        ttl = max(0, int(state.expires_at - current))
        session_id = str(state.record.get("session_id") or "").strip()
        if not session_id or not state.authority_key or ttl <= 0:
            if session_id:
                await self.delete(session_id, authority_key=state.authority_key)
            return
        payload = {
            "schema": _SCHEMA,
            "record": state.record,
            "authority_key": state.authority_key,
            "revision": state.revision,
            "expires_at": state.expires_at,
        }
        try:
            encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            await self._redis.setex(self._session_key(session_id), ttl, encoded)
            await self._redis.setex(
                self._authority_key(state.authority_key),
                ttl,
                session_id,
            )
        except Exception:
            logger.warning("Platform session projection write failed", exc_info=True)

    async def delete(self, session_id: str, *, authority_key: str = "") -> None:
        try:
            keys = [self._session_key(session_id)]
            if authority_key:
                keys.append(self._authority_key(authority_key))
            await self._redis.delete(*keys)
        except Exception:
            logger.warning("Platform session projection delete failed", exc_info=True)


class ProjectedPlatformSessionStore:
    """Durable authority with a PostgreSQL-fenced Redis projection."""

    def __init__(
        self,
        *,
        authority: PlatformSessionFenceStore,
        projection: RedisPlatformSessionProjection,
    ) -> None:
        self._authority = authority
        self._projection = projection

    async def _validated(
        self,
        cached: PlatformSessionAuthorityState | None,
        *,
        now: float,
    ) -> PlatformSessionAuthorityState | None:
        if cached is None:
            return None
        session_id = str(cached.record.get("session_id") or "")
        fence = await self._authority.get_session_fence(session_id)
        if (
            fence is not None
            and fence.state == "active"
            and fence.expires_at > now
            and fence.authority_key == cached.authority_key
            and fence.revision == cached.revision
            and abs(fence.expires_at - cached.expires_at) < 0.001
        ):
            return cached
        await self._projection.delete(
            session_id,
            authority_key=cached.authority_key,
        )
        return None

    async def get_or_create(
        self,
        *,
        authority_key: str,
        candidate: Mapping[str, Any],
        user_data: Mapping[str, Any],
        request_context: Mapping[str, Any],
        user_type: str,
        ttl_seconds: int,
        now: float,
    ) -> PlatformSessionStoreResult:
        cached = await self._validated(
            await self._projection.read_by_authority(authority_key),
            now=now,
        )
        if cached is not None:
            merged = _merge_record(
                cached.record,
                user_data=user_data,
                request_context=request_context,
                user_type=user_type,
            )
            if (
                merged == cached.record
                and cached.expires_at >= now + (max(1, int(ttl_seconds)) / 2)
            ):
                return PlatformSessionStoreResult(
                    record=dict(cached.record),
                    created=False,
                    revision=cached.revision,
                    authority_key=cached.authority_key,
                    expires_at=cached.expires_at,
                )
        result = await self._authority.get_or_create(
            authority_key=authority_key,
            candidate=candidate,
            user_data=user_data,
            request_context=request_context,
            user_type=user_type,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        await self._projection.write(
            PlatformSessionAuthorityState(
                record=result.record,
                authority_key=result.authority_key or authority_key,
                revision=result.revision,
                expires_at=result.expires_at or now + max(1, int(ttl_seconds)),
            ),
            now=now,
        )
        return result

    async def update_session(
        self,
        record: Mapping[str, Any],
        *,
        ttl_seconds: int,
        now: float,
    ) -> bool:
        updated = await self._authority.update_session(
            record,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        session_id = str(record.get("session_id") or "")
        state = (
            await self._authority.get_session_state_by_id(session_id)
            if updated
            else None
        )
        if state is not None:
            await self._projection.write(state, now=now)
        else:
            await self._projection.delete(session_id)
        return updated

    async def get_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        now = time.time()
        cached = await self._validated(
            await self._projection.read_by_session(session_id),
            now=now,
        )
        if cached is not None:
            return dict(cached.record)
        state = await self._authority.get_session_state_by_id(session_id)
        if state is None:
            return None
        await self._projection.write(state, now=now)
        return dict(state.record)

    async def get_session_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        state = await self._authority.get_session_state_by_user_id(user_id)
        if state is None:
            return None
        await self._projection.write(state)
        return dict(state.record)


__all__ = [
    "PlatformSessionFenceStore",
    "ProjectedPlatformSessionStore",
    "RedisPlatformSessionProjection",
]
