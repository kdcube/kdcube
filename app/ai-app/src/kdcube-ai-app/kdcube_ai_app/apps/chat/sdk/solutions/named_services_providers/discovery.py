# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Mapping

from .registry import NamedServiceRegistry
from .types import (
    NamedServiceProviderSpec,
    NamedServiceRequest,
    build_default_operations,
    normalize_search_scopes,
    namespace_for_ref,
)


NAMED_SERVICE_DISCOVERY_SCHEMA = "kdcube.named_service.discovery.v1"
DEFAULT_DISCOVERY_TTL_SECONDS = 0
LOGGER = logging.getLogger("kdcube.sdk.named_services.discovery")

_DISCOVERY_UNSET = object()
_DISCOVERY_CV: ContextVar[Any] = ContextVar("kdcube_named_service_discovery", default=_DISCOVERY_UNSET)


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _key_part(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_", ".", "@", ":"} else "_" for ch in text) or "_"


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _report_list(values: Any) -> str:
    items = [str(item) for item in (values or ()) if str(item or "").strip()]
    if not items:
        return "    - <none>"
    return "\n".join(f"    - {item}" for item in items)


def _redis_client_from_settings() -> Any:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    from kdcube_ai_app.infra.redis.client import get_async_redis_client

    return get_async_redis_client(get_settings().REDIS_URL)


def _portable_context_for_discovery(discovery: Any) -> dict[str, Any]:
    if isinstance(discovery, RedisNamedServiceDiscovery):
        return {
            "schema": NAMED_SERVICE_DISCOVERY_SCHEMA,
            "backend": "redis",
            "tenant": discovery.tenant,
            "project": discovery.project,
        }
    return {}


def _get_portable_context() -> dict[str, Any]:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_named_service_discovery_context

        value = get_current_named_service_discovery_context()
    except Exception:
        value = {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _set_portable_context(context: Mapping[str, Any] | None) -> None:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_current_named_service_discovery_context

        set_current_named_service_discovery_context(context)
    except Exception:
        LOGGER.debug("Failed to bind named-service discovery portable context", exc_info=True)


def _discovery_from_portable_context() -> Any:
    context = _get_portable_context()
    if str(context.get("schema") or "") != NAMED_SERVICE_DISCOVERY_SCHEMA:
        return None
    if str(context.get("backend") or "") != "redis":
        return None
    tenant = str(context.get("tenant") or "").strip()
    project = str(context.get("project") or "").strip()
    if not tenant or not project:
        return None
    return RedisNamedServiceDiscovery(
        _redis_client_from_settings(),
        tenant=tenant,
        project=project,
    )


def _discovery_from_request_context() -> Any:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

        request_context = get_current_request_context()
    except Exception:
        request_context = None
    actor = getattr(request_context, "actor", None) if request_context is not None else None
    tenant = str(getattr(actor, "tenant_id", None) or "").strip()
    project = str(getattr(actor, "project_id", None) or "").strip()
    if not tenant or not project:
        return None
    return RedisNamedServiceDiscovery(
        _redis_client_from_settings(),
        tenant=tenant,
        project=project,
    )


def _object_kind_from_request(request: NamedServiceRequest) -> str:
    for source in (request.payload, request.object, request.filters):
        if not isinstance(source, Mapping):
            continue
        value = str(source.get("object_kind") or source.get("kind") or "").strip()
        if value:
            return value
    return ""


def _provider_priority(spec: NamedServiceProviderSpec) -> int:
    try:
        return int((spec.metadata or {}).get("priority") or 0)
    except Exception:
        return 0


def _normalize_provider_operations(value: Any) -> Mapping[str, Any]:
    if value in (None, ""):
        return build_default_operations()
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (list, tuple, set)):
        return {str(item): {"transports": ["local"]} for item in value if str(item or "").strip()}
    return build_default_operations()


def _spec_from_provider_config(config: Mapping[str, Any], *, namespace: str = "") -> NamedServiceProviderSpec:
    cfg = dict(config or {})
    provider_id = str(cfg.get("provider_id") or cfg.get("provider") or cfg.get("id") or "").strip()
    return NamedServiceProviderSpec(
        provider_id=provider_id,
        bundle_id=str(cfg.get("bundle_id") or "").strip() or None,
        namespace=str(cfg.get("namespace") or namespace or "").strip().lower().rstrip(":") or None,
        namespaces=tuple(str(item).strip().lower().rstrip(":") for item in (cfg.get("namespaces") or ()) if str(item).strip()),
        refs=tuple(str(item).strip() for item in (cfg.get("refs") or ()) if str(item).strip()),
        object_kinds=tuple(str(item).strip() for item in (cfg.get("object_kinds") or ()) if str(item).strip()),
        search_scopes=normalize_search_scopes(
            cfg.get("search_scopes") or cfg.get("searchScopes"),
            default_namespace=str(cfg.get("namespace") or namespace or "").strip().lower().rstrip(":") or None,
        ),
        operations=_normalize_provider_operations(cfg.get("operations")),
        label=str(cfg.get("label") or "").strip() or None,
        description=str(cfg.get("description") or "").strip() or None,
        metadata=dict(cfg.get("metadata") or {}),
    )


@dataclass(frozen=True)
class NamedServiceDiscoveryEntry:
    spec: NamedServiceProviderSpec
    endpoint: dict[str, Any] = field(default_factory=dict)
    registered_at: float = 0.0
    expires_at: float = 0.0
    schema: str = NAMED_SERVICE_DISCOVERY_SCHEMA

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NamedServiceDiscoveryEntry":
        data = dict(value or {})
        return cls(
            spec=NamedServiceProviderSpec.from_dict(data.get("spec") or {}),
            endpoint=dict(data.get("endpoint") or {}),
            registered_at=float(data.get("registered_at") or 0.0),
            expires_at=float(data.get("expires_at") or 0.0),
            schema=str(data.get("schema") or NAMED_SERVICE_DISCOVERY_SCHEMA),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "spec": self.spec.to_dict(),
            "endpoint": dict(self.endpoint or {}),
            "registered_at": self.registered_at,
            "expires_at": self.expires_at,
        }


class RedisNamedServiceDiscovery:
    """Redis-backed named-service discovery table for one tenant/project."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        ttl_seconds: int | None = DEFAULT_DISCOVERY_TTL_SECONDS,
    ) -> None:
        self.redis = redis
        self.tenant = str(tenant or "").strip()
        self.project = str(project or "").strip()
        self.ttl_seconds = max(0, int(ttl_seconds if ttl_seconds is not None else DEFAULT_DISCOVERY_TTL_SECONDS))
        self._base = (
            f"kdcube:named_services:{_key_part(self.tenant)}:{_key_part(self.project)}"
        )

    def _provider_uid(self, *, bundle_id: str, provider_id: str) -> str:
        return f"{_key_part(bundle_id)}::{_key_part(provider_id)}"

    def _provider_key(self, uid: str) -> str:
        return f"{self._base}:provider:{uid}"

    def _all_key(self) -> str:
        return f"{self._base}:providers"

    def _namespace_key(self, namespace: str) -> str:
        return f"{self._base}:namespace:{_key_part(namespace)}"

    async def register_registry(
        self,
        registry: NamedServiceRegistry,
        *,
        bundle_id: str,
        transport: str = "bundle_registry",
        registry_method: str = "named_services",
        ttl_seconds: int | None = None,
    ) -> list[NamedServiceDiscoveryEntry]:
        entries: list[NamedServiceDiscoveryEntry] = []
        for provider in registry.providers():
            entry = await self.register_provider(
                provider.spec,
                bundle_id=bundle_id,
                transport=transport,
                registry_method=registry_method,
                ttl_seconds=ttl_seconds,
            )
            entries.append(entry)
        return entries

    async def register_provider(
        self,
        spec: NamedServiceProviderSpec,
        *,
        bundle_id: str,
        transport: str = "bundle_registry",
        registry_method: str = "named_services",
        endpoint: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> NamedServiceDiscoveryEntry:
        resolved_bundle_id = str(bundle_id or spec.bundle_id or "").strip()
        if not resolved_bundle_id:
            raise ValueError("named-service discovery registration requires bundle_id")
        ttl = max(0, int(ttl_seconds if ttl_seconds is not None else self.ttl_seconds))
        now = time.time()
        endpoint_payload = {
            "transport": str(transport or "bundle_registry").strip() or "bundle_registry",
            "bundle_id": resolved_bundle_id,
            "provider": spec.provider_id,
            "registry_method": str(registry_method or "named_services").strip() or "named_services",
        }
        endpoint_payload.update(dict(endpoint or {}))
        entry = NamedServiceDiscoveryEntry(
            spec=NamedServiceProviderSpec.from_dict({**spec.to_dict(), "bundle_id": resolved_bundle_id}),
            endpoint=endpoint_payload,
            registered_at=now,
            expires_at=now + ttl if ttl > 0 else 0.0,
        )
        uid = self._provider_uid(bundle_id=resolved_bundle_id, provider_id=spec.provider_id)
        provider_key = self._provider_key(uid)
        await self.redis.set(provider_key, _json_dumps(entry.to_dict()), ex=ttl if ttl > 0 else None)
        await self.redis.sadd(self._all_key(), provider_key)
        if ttl > 0:
            await self.redis.expire(self._all_key(), ttl * 2)
        else:
            await self._persist_if_supported(self._all_key())
        for namespace in entry.spec.namespaces or ():
            ns_key = self._namespace_key(namespace)
            await self.redis.sadd(ns_key, provider_key)
            if ttl > 0:
                await self.redis.expire(ns_key, ttl * 2)
            else:
                await self._persist_if_supported(ns_key)
        LOGGER.info(
            "Named-service discovery provider registered:\n"
            "  scope: tenant=%s project=%s\n"
            "  provider: %s\n"
            "  bundle: %s\n"
            "  namespaces:\n%s\n"
            "  endpoint:\n"
            "    transport: %s\n"
            "    registry_method: %s\n"
            "    redis_key: %s\n"
            "  retention:\n"
            "    ttl_seconds: %s\n"
            "    expires_at: %s\n"
            "  refs:\n%s\n"
            "  object_kinds:\n%s\n"
            "  search_scopes:\n%s\n"
            "  operations:\n%s",
            self.tenant,
            self.project,
            spec.provider_id,
            resolved_bundle_id,
            _report_list(entry.spec.namespaces),
            endpoint_payload.get("transport") or "",
            endpoint_payload.get("registry_method") or "",
            provider_key,
            ttl,
            "persistent" if entry.expires_at <= 0 else entry.expires_at,
            _report_list(entry.spec.refs),
            _report_list(entry.spec.object_kinds),
            _report_list(scope.namespace for scope in (entry.spec.search_scopes or ())),
            _report_list(sorted((entry.spec.operations or {}).keys())),
        )
        return entry

    async def providers(self, *, namespace: str = "") -> list[NamedServiceDiscoveryEntry]:
        keys = await self._provider_keys(namespace=namespace)
        entries: list[NamedServiceDiscoveryEntry] = []
        missing = 0
        for key in keys:
            raw = await self.redis.get(key)
            if not raw:
                missing += 1
                continue
            try:
                entries.append(NamedServiceDiscoveryEntry.from_dict(json.loads(_decode(raw))))
            except Exception:
                LOGGER.warning(
                    "Named-service discovery skipped unreadable provider record: tenant=%s project=%s namespace=%s provider_key=%s",
                    self.tenant,
                    self.project,
                    namespace or "",
                    key,
                    exc_info=True,
                )
                continue
        LOGGER.info(
            "Named-service discovery provider scan:\n"
            "  scope: tenant=%s project=%s\n"
            "  namespace: %s\n"
            "  provider_keys: %s\n"
            "  usable_records: %s\n"
            "  missing_records: %s",
            self.tenant,
            self.project,
            namespace or "<all>",
            len(keys),
            len(entries),
            missing,
        )
        return entries

    async def resolve(
        self,
        request: NamedServiceRequest,
        *,
        namespace: str = "",
        provider_id: str = "",
    ) -> NamedServiceDiscoveryEntry | None:
        ns = str(namespace or request.namespace or namespace_for_ref(request.object_ref) or "").strip().lower()
        provider = str(provider_id or request.provider or "").strip()
        object_ref = str(request.object_ref or "").strip()
        object_kind = _object_kind_from_request(request)
        candidates = await self.providers(namespace=ns)
        if not candidates and provider:
            candidates = await self.providers()
        filtered: list[NamedServiceDiscoveryEntry] = []
        for entry in candidates:
            spec = entry.spec
            if provider and spec.provider_id != provider:
                continue
            if request.operation and not spec.supports_operation(request.operation):
                continue
            if object_ref and not (spec.matches_ref(object_ref) or namespace_for_ref(object_ref) in set(spec.namespaces or ())):
                continue
            if object_kind and spec.object_kinds and object_kind not in set(spec.object_kinds or ()):
                continue
            filtered.append(entry)
        filtered.sort(key=lambda entry: self._rank(entry, request=request, namespace=ns), reverse=True)
        return filtered[0] if filtered else None

    async def _provider_keys(self, *, namespace: str = "") -> list[str]:
        key = self._namespace_key(namespace) if namespace else self._all_key()
        raw = await self.redis.smembers(key)
        return sorted(_decode(item) for item in (raw or []) if _decode(item))

    async def _persist_if_supported(self, key: str) -> None:
        persist = getattr(self.redis, "persist", None)
        if callable(persist):
            await persist(key)

    def _rank(self, entry: NamedServiceDiscoveryEntry, *, request: NamedServiceRequest, namespace: str) -> tuple[int, int, int, str]:
        spec = entry.spec
        object_ref = str(request.object_ref or "").strip()
        ref_score = spec.match_score(object_ref) if object_ref else 0
        object_kind = _object_kind_from_request(request)
        kind_score = 1 if object_kind and object_kind in set(spec.object_kinds or ()) else 0
        namespace_score = 1 if namespace and namespace in set(spec.namespaces or ()) else 0
        return (ref_score, kind_score, namespace_score + _provider_priority(spec), spec.provider_id)


class ConfiguredNamedServiceDiscovery:
    """In-memory Named Service Discovery view built from explicit provider config."""

    def __init__(
        self,
        provider_configs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        namespace: str = "",
    ) -> None:
        self.namespace = str(namespace or "").strip().lower().rstrip(":")
        self._entries: list[NamedServiceDiscoveryEntry] = []
        for raw in provider_configs or ():
            if not isinstance(raw, Mapping):
                continue
            spec = _spec_from_provider_config(raw, namespace=self.namespace)
            endpoint = {
                "transport": str(raw.get("transport") or "bundle_registry").strip() or "bundle_registry",
                "bundle_id": str(raw.get("bundle_id") or spec.bundle_id or "").strip(),
                "provider": spec.provider_id,
                "operation": str(raw.get("operation") or "named_service").strip() or "named_service",
                "route": str(raw.get("route") or "operations").strip() or "operations",
                "registry_method": str(raw.get("registry_method") or "named_services").strip() or "named_services",
            }
            self._entries.append(
                NamedServiceDiscoveryEntry(
                    spec=spec,
                    endpoint={key: value for key, value in endpoint.items() if value not in (None, "")},
                )
            )

    async def resolve(
        self,
        request: NamedServiceRequest,
        *,
        namespace: str = "",
        provider_id: str = "",
    ) -> NamedServiceDiscoveryEntry | None:
        ns = str(namespace or request.namespace or namespace_for_ref(request.object_ref) or self.namespace or "").strip().lower()
        provider = str(provider_id or request.provider or "").strip()
        object_ref = str(request.object_ref or "").strip()
        object_kind = _object_kind_from_request(request)
        filtered: list[NamedServiceDiscoveryEntry] = []
        for entry in self._entries:
            spec = entry.spec
            if provider and spec.provider_id != provider:
                continue
            if request.operation and not spec.supports_operation(request.operation):
                continue
            if ns and ns not in set(spec.namespaces or ()):
                continue
            if object_ref and not (spec.matches_ref(object_ref) or namespace_for_ref(object_ref) in set(spec.namespaces or ())):
                continue
            if object_kind and spec.object_kinds and object_kind not in set(spec.object_kinds or ()):
                continue
            filtered.append(entry)
        filtered.sort(key=lambda entry: self._rank(entry, request=request, namespace=ns), reverse=True)
        return filtered[0] if filtered else None

    def _rank(self, entry: NamedServiceDiscoveryEntry, *, request: NamedServiceRequest, namespace: str) -> tuple[int, int, int, str]:
        spec = entry.spec
        object_ref = str(request.object_ref or "").strip()
        ref_score = spec.match_score(object_ref) if object_ref else 0
        object_kind = _object_kind_from_request(request)
        kind_score = 1 if object_kind and object_kind in set(spec.object_kinds or ()) else 0
        namespace_score = 1 if namespace and namespace in set(spec.namespaces or ()) else 0
        return (ref_score, kind_score, namespace_score + _provider_priority(spec), spec.provider_id)


@contextmanager
def bind_named_service_discovery(discovery: Any):
    token = _DISCOVERY_CV.set(discovery)
    previous_portable_context = _get_portable_context()
    _set_portable_context(_portable_context_for_discovery(discovery))
    try:
        yield discovery
    finally:
        _set_portable_context(previous_portable_context)
        _DISCOVERY_CV.reset(token)


def get_current_named_service_discovery() -> Any:
    value = _DISCOVERY_CV.get()
    if value is not _DISCOVERY_UNSET:
        return value
    restored = _discovery_from_portable_context()
    if restored is not None:
        return restored
    return _discovery_from_request_context()


__all__ = [
    "DEFAULT_DISCOVERY_TTL_SECONDS",
    "NAMED_SERVICE_DISCOVERY_SCHEMA",
    "ConfiguredNamedServiceDiscovery",
    "NamedServiceDiscoveryEntry",
    "RedisNamedServiceDiscovery",
    "bind_named_service_discovery",
    "get_current_named_service_discovery",
]
