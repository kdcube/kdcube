# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Redis projection over the durable bundle-session authority."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Mapping, Protocol

from kdcube_ai_app.auth.bundle.session_store import (
    BundleSessionLoginState,
    BundleSessionStore,
    BundleSessionValidationFence,
    BundleSessionValidationState,
)
from kdcube_ai_app.auth.session_projection_support import (
    decode_session_projection,
    session_projection_generation_scope,
)
from kdcube_ai_app.infra.namespaces import ns_key

logger = logging.getLogger(__name__)

_SCHEMA = "kdcube.bundle-session-projection.v1"


class BundleSessionFenceStore(BundleSessionStore, Protocol):
    async def get_validation_fence(
        self,
        session_id: str,
    ) -> BundleSessionValidationFence | None: ...


def _state_from_payload(payload: Mapping[str, Any]) -> BundleSessionValidationState | None:
    if payload.get("schema") != _SCHEMA:
        return None
    try:
        return BundleSessionValidationState(
            session=dict(payload.get("session") or {}),
            session_state=str(payload.get("session_state") or ""),
            idle_expires_at=int(payload.get("idle_expires_at") or 0),
            hard_expires_at=int(payload.get("hard_expires_at") or 0),
            user=dict(payload.get("user") or {}),
            user_state=str(payload.get("user_state") or ""),
            user_disabled=bool(payload.get("user_disabled")),
            version=int(payload.get("version") or 0),
            session_revision=int(payload.get("session_revision") or 0),
            user_revision=int(payload.get("user_revision") or 0),
        )
    except (TypeError, ValueError):
        return None


def _fence_matches(
    state: BundleSessionValidationState,
    fence: BundleSessionValidationFence,
) -> bool:
    return (
        state.session_state == fence.session_state
        and state.idle_expires_at == fence.idle_expires_at
        and state.hard_expires_at == fence.hard_expires_at
        and state.user_state == fence.user_state
        and state.user_disabled == fence.user_disabled
        and state.version == fence.version
        and state.session_revision == fence.session_revision
        and state.user_revision == fence.user_revision
    )


class RedisBundleSessionProjection:
    """Generation-scoped, TTL-bound copy of bundle session read state."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        generation_id: str,
    ) -> None:
        if redis is None:
            raise ValueError("bundle session projection requires redis")
        self._redis = redis
        generation = session_projection_generation_scope(generation_id)
        self._prefix = ns_key(
            f"kdcube:auth:bundle-session:projection:{generation}",
            tenant=tenant,
            project=project,
        )

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}:session:{session_id}"

    def _user_key(self, subject: str) -> str:
        digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        return f"{self._prefix}:user-sessions:{digest}"

    async def read(self, session_id: str) -> BundleSessionValidationState | None:
        key = self._session_key(session_id)
        try:
            payload = decode_session_projection(await self._redis.get(key))
            state = _state_from_payload(payload or {}) if payload else None
            if payload is not None and state is None:
                await self._redis.delete(key)
            return state
        except Exception:
            logger.warning("Bundle session projection read failed", exc_info=True)
            return None

    async def write(
        self,
        state: BundleSessionValidationState,
        *,
        now: int | None = None,
    ) -> None:
        current = int(time.time() if now is None else now)
        expires_at = min(state.idle_expires_at, state.hard_expires_at)
        ttl = expires_at - current
        index_ttl = state.hard_expires_at - current
        session_id = str(state.session.get("session_id") or "").strip()
        subject = str(state.session.get("sub") or state.user.get("sub") or "").strip()
        if not session_id or ttl <= 0:
            if session_id:
                await self.delete(session_id, subject=subject)
            return
        payload = {
            "schema": _SCHEMA,
            "session": state.session,
            "session_state": state.session_state,
            "idle_expires_at": state.idle_expires_at,
            "hard_expires_at": state.hard_expires_at,
            "user": state.user,
            "user_state": state.user_state,
            "user_disabled": state.user_disabled,
            "version": state.version,
            "session_revision": state.session_revision,
            "user_revision": state.user_revision,
        }
        try:
            await self._redis.setex(
                self._session_key(session_id),
                ttl,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            )
            if subject:
                user_key = self._user_key(subject)
                await self._redis.sadd(user_key, session_id)
                await self._redis.expire(user_key, max(ttl, index_ttl))
        except Exception:
            logger.warning("Bundle session projection write failed", exc_info=True)

    async def delete(self, session_id: str, *, subject: str = "") -> None:
        try:
            await self._redis.delete(self._session_key(session_id))
            if subject:
                await self._redis.srem(self._user_key(subject), session_id)
        except Exception:
            logger.warning("Bundle session projection delete failed", exc_info=True)

    async def delete_user(self, subject: str) -> None:
        user_key = self._user_key(subject)
        try:
            values = await self._redis.smembers(user_key)
            session_ids = [
                value.decode("utf-8")
                if isinstance(value, (bytes, bytearray))
                else str(value)
                for value in (values or ())
            ]
            keys = [self._session_key(value) for value in session_ids]
            if keys:
                await self._redis.delete(*keys)
            await self._redis.delete(user_key)
        except Exception:
            logger.warning("Bundle user projection delete failed", exc_info=True)


class ProjectedBundleSessionStore:
    """Durable authority with a fenced Redis read projection."""

    def __init__(
        self,
        *,
        authority: BundleSessionFenceStore,
        projection: RedisBundleSessionProjection,
    ) -> None:
        self._authority = authority
        self._projection = projection

    async def register_user(
        self,
        *,
        sub: str,
        updates: Mapping[str, Any],
        now: int,
    ) -> dict[str, Any]:
        record = await self._authority.register_user(sub=sub, updates=updates, now=now)
        await self._projection.delete_user(sub)
        return record

    async def get_user(self, sub: str) -> dict[str, Any] | None:
        return await self._authority.get_user(sub)

    async def get_login_state(self, sub: str) -> BundleSessionLoginState | None:
        return await self._authority.get_login_state(sub)

    async def issue_session(
        self,
        record: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> bool:
        created = await self._authority.issue_session(
            record,
            expected_version=expected_version,
        )
        if created:
            state = await self._authority.get_validation_state(
                str(record.get("session_id") or "")
            )
            if state is not None:
                await self._projection.write(state)
        return created

    async def get_validation_state(
        self,
        session_id: str,
    ) -> BundleSessionValidationState | None:
        cached = await self._projection.read(session_id)
        fence = await self._authority.get_validation_fence(session_id)
        if fence is None:
            await self._projection.delete(
                session_id,
                subject=str((cached.session if cached else {}).get("sub") or ""),
            )
            return None
        if cached is not None and _fence_matches(cached, fence):
            return cached
        state = await self._authority.get_validation_state(session_id)
        if state is None:
            await self._projection.delete(session_id)
            return None
        await self._projection.write(state)
        return state

    async def get_validation_fence(
        self,
        session_id: str,
    ) -> BundleSessionValidationFence | None:
        return await self._authority.get_validation_fence(session_id)

    async def touch_session(
        self,
        session_id: str,
        *,
        expires_at: int,
        now: int,
    ) -> dict[str, Any] | None:
        record = await self._authority.touch_session(
            session_id,
            expires_at=expires_at,
            now=now,
        )
        state = (
            await self._authority.get_validation_state(session_id)
            if record is not None
            else None
        )
        if state is None:
            await self._projection.delete(session_id)
        else:
            await self._projection.write(state, now=now)
        return record

    async def revoke_session(self, session_id: str) -> bool:
        revoked = await self._authority.revoke_session(session_id)
        await self._projection.delete(session_id)
        return revoked

    async def invalidate_user(self, sub: str) -> int:
        count = await self._authority.invalidate_user(sub)
        await self._projection.delete_user(sub)
        return count

    async def delete_user(self, sub: str) -> bool:
        deleted = await self._authority.delete_user(sub)
        await self._projection.delete_user(sub)
        return deleted


__all__ = [
    "BundleSessionFenceStore",
    "ProjectedBundleSessionStore",
    "RedisBundleSessionProjection",
]
