# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Resolve verified sign-in identities to stable platform principals.

Connection Hub owns the durable identity edge. Redis is its shared serving
projection, so distributed workers do not read bundle storage on the normal
path. A new verified identity creates its own provider-prefixed principal;
another verified identity reaches that principal only through an explicit
edge.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Mapping

from connection_hub.hub.edge_cache import (
    ConnectionEdgeRuntimeCache,
    ConnectionEdgeRuntimeCacheError,
)
from connection_hub.hub.edges import (
    ConnectionEdgeStore,
    ConnectionEdgeStoreError,
    edge_actor,
    edge_target,
)
from connection_hub.server_side_login.model import VerifiedIdentity

from kdcube_ai_app.auth.AuthManager import AuthenticationError, User
from kdcube_ai_app.auth.bundle.sessions import BundleSessionAuthority, BundleSessionError

logger = logging.getLogger(__name__)


def _str(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class PlatformPrincipalResolution:
    user_id: str
    source: str
    edge_id: str = ""


def identity_authority(identity: VerifiedIdentity, *, configured: str = "") -> str:
    """Return the verified issuer realm used with ``sub`` as identity."""

    claims = identity.claims if isinstance(identity.claims, Mapping) else {}
    return _str(claims.get("iss")) or _str(configured) or _str(identity.provider).lower()


def canonical_platform_user_id(*, provider: str, subject: str) -> str:
    provider_value = _str(provider).lower()
    subject_value = _str(subject)
    if not provider_value or not subject_value:
        raise AuthenticationError("Verified identity lacks provider or subject")
    return f"{provider_value}:{subject_value}"


class PlatformPrincipalResolver:
    """Redis-first resolver with durable Connection Hub read-through."""

    def __init__(
        self,
        *,
        authority: BundleSessionAuthority,
        edge_store: ConnectionEdgeStore,
        tenant: str,
        project: str,
        configured_authority_id: str = "",
    ) -> None:
        self._authority = authority
        self._edge_store = edge_store
        self._tenant = _str(tenant) or "default"
        self._project = _str(project) or "default"
        self._configured_authority_id = _str(configured_authority_id)

    async def _cache(self) -> ConnectionEdgeRuntimeCache:
        try:
            redis = await self._authority._redis_client()
        except Exception as exc:
            raise AuthenticationError("Platform identity cache is unavailable") from exc
        return ConnectionEdgeRuntimeCache(
            redis,
            tenant=self._tenant,
            project=self._project,
        )

    @staticmethod
    async def _read_cache(
        cache: ConnectionEdgeRuntimeCache,
        *,
        authority_id: str,
        subject: str,
    ) -> dict[str, Any] | None:
        try:
            return await cache.read(authority_id=authority_id, subject=subject)
        except Exception as exc:
            raise AuthenticationError("Platform identity cache is unavailable") from exc

    @asynccontextmanager
    async def _resolution_lock(
        self,
        cache: ConnectionEdgeRuntimeCache,
    ):
        try:
            async with cache.mutation_lock():
                yield
        except ConnectionEdgeRuntimeCacheError as exc:
            raise AuthenticationError("Platform identity cache is unavailable") from exc

    def _durable_edge(
        self,
        *,
        authority_id: str,
        provider: str,
        subject: str,
    ) -> Mapping[str, Any] | None:
        rows = self._edge_store.list_edges(
            source_provider=provider,
            source_subject=subject,
        )
        exact = [
            edge
            for edge in rows
            if _str(edge_actor(edge).get("authority_id")) == authority_id
        ]
        # Older manually-created edges used the provider as their authority.
        # Accept one only when it agrees with every exact candidate, then write
        # the issuer-scoped form below.
        compatible = [
            edge
            for edge in rows
            if _str(edge_actor(edge).get("authority_id")) == provider
        ]
        candidates = [*exact, *compatible]
        targets = {_str(edge_target(edge).get("user_id")) for edge in candidates}
        targets.discard("")
        if len(targets) > 1:
            raise ConnectionEdgeStoreError(
                "verified identity has conflicting platform targets"
            )
        if exact:
            return exact[0]
        return compatible[0] if compatible else None

    async def _legacy_user_id(self, identity: VerifiedIdentity) -> str:
        """Recognize only the old raw-sub record created by this authenticator."""

        try:
            user = await self._authority.get_user(_str(identity.subject))
        except BundleSessionError as exc:
            raise AuthenticationError("Platform user registry is unavailable") from exc
        if user is None:
            return ""
        if _str(user.provider_subject) != _str(identity.subject):
            return ""
        if _str(user.provider).lower() != _str(identity.provider).lower():
            return ""
        return _str(user.sub)

    async def _publish(self, cache: ConnectionEdgeRuntimeCache, edge: Mapping[str, Any]) -> PlatformPrincipalResolution:
        try:
            payload = await cache.publish_edge(edge)
        except Exception as exc:
            raise AuthenticationError("Platform identity cache is unavailable") from exc
        return PlatformPrincipalResolution(
            user_id=_str(payload.get("platform_user_id")),
            source="connection_edge",
            edge_id=_str(payload.get("edge_id")),
        )

    async def resolve(self, identity: VerifiedIdentity) -> PlatformPrincipalResolution:
        provider = _str(identity.provider).lower()
        subject = _str(identity.subject)
        authority_id = identity_authority(
            identity,
            configured=self._configured_authority_id,
        )
        canonical = canonical_platform_user_id(provider=provider, subject=subject)
        cache = await self._cache()

        cached = await self._read_cache(
            cache,
            authority_id=authority_id,
            subject=subject,
        )
        if cached is not None:
            return PlatformPrincipalResolution(
                user_id=_str(cached.get("platform_user_id")),
                source="connection_edge_cache",
                edge_id=_str(cached.get("edge_id")),
            )

        async with self._resolution_lock(cache):
            cached = await self._read_cache(
                cache,
                authority_id=authority_id,
                subject=subject,
            )
            if cached is not None:
                return PlatformPrincipalResolution(
                    user_id=_str(cached.get("platform_user_id")),
                    source="connection_edge_cache",
                    edge_id=_str(cached.get("edge_id")),
                )
            try:
                edge = await asyncio.to_thread(
                    self._durable_edge,
                    authority_id=authority_id,
                    provider=provider,
                    subject=subject,
                )
                if edge is not None:
                    target_user_id = _str(edge_target(edge).get("user_id"))
                    # Migrate provider-scoped legacy edges to the verified
                    # issuer realm while preserving their chosen principal.
                    if _str(edge_actor(edge).get("authority_id")) != authority_id:
                        edge = await asyncio.to_thread(
                            self._edge_store.upsert_edge,
                            from_authority_id=authority_id,
                            from_provider=provider,
                            from_subject=subject,
                            to_user_id=target_user_id,
                            created_by=target_user_id,
                            proof={"type": "verified_sign_in_edge_migration"},
                        )
                        await asyncio.to_thread(
                            self._edge_store.remove_edge,
                            from_authority_id=provider,
                            from_provider=provider,
                            from_subject=subject,
                            target_user_id=target_user_id,
                        )
                        await cache.remove(
                            authority_id=provider,
                            subject=subject,
                        )
                    return await self._publish(cache, edge)

                legacy_user_id = await self._legacy_user_id(identity)
                target_user_id = legacy_user_id or canonical
                edge = await asyncio.to_thread(
                    self._edge_store.upsert_edge,
                    from_authority_id=authority_id,
                    from_provider=provider,
                    from_subject=subject,
                    to_user_id=target_user_id,
                    created_by=target_user_id,
                    proof={
                        "type": (
                            "verified_sign_in_legacy_transition"
                            if legacy_user_id
                            else "verified_first_sign_in"
                        )
                    },
                    metadata={"automatic": True},
                )
            except (ConnectionEdgeStoreError, OSError, ValueError) as exc:
                logger.error(
                    "Platform principal resolution failed provider=%s authority=%s",
                    provider,
                    authority_id,
                    exc_info=True,
                )
                raise AuthenticationError(
                    "Platform identity mapping is unavailable"
                ) from exc
            resolution = await self._publish(cache, edge)
            return PlatformPrincipalResolution(
                user_id=resolution.user_id,
                source=(
                    "legacy_platform_user"
                    if legacy_user_id
                    else "first_verified_sign_in"
                ),
                edge_id=resolution.edge_id,
            )

    async def map_token_user(
        self,
        user: User,
        *,
        provider: str,
        configured_authority_id: str = "",
    ) -> User:
        provider_subject = _str(getattr(user, "sub", None))
        source_authority = _str(getattr(user, "issuer", None)) or _str(
            configured_authority_id
        )
        identity = VerifiedIdentity(
            provider=_str(provider).lower(),
            subject=provider_subject,
            email=_str(getattr(user, "email", None)),
            name=_str(getattr(user, "name", None)),
            claims={"iss": source_authority},
        )
        resolution = await self.resolve(identity)
        mapped = user.model_copy(deep=True)
        setattr(mapped, "sub", resolution.user_id)
        return mapped


__all__ = [
    "PlatformPrincipalResolution",
    "PlatformPrincipalResolver",
    "canonical_platform_user_id",
    "identity_authority",
]
