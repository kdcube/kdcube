# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import NamedServiceProviderSpec, namespace_for_ref


@dataclass(frozen=True)
class RegisteredNamedServiceProvider:
    spec: NamedServiceProviderSpec
    provider: Any


class NamedServiceRegistry:
    """Local registry for named service providers.

    The registry is intentionally in-process. API/MCP/Data Bus discovery can
    later use the same specs while keeping this local dispatch path as the
    fastest option for composed bundles.
    """

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredNamedServiceProvider] = {}
        self._namespace_index: dict[str, list[str]] = {}

    def register(self, provider: Any, spec: NamedServiceProviderSpec | None = None) -> RegisteredNamedServiceProvider:
        provider_spec = spec or getattr(provider, "spec", None) or getattr(
            provider,
            "__kdcube_named_service_provider__",
            None,
        )
        if provider_spec is None:
            raise ValueError("named service provider spec is required")
        if not isinstance(provider_spec, NamedServiceProviderSpec):
            provider_spec = NamedServiceProviderSpec.from_dict(provider_spec)
        provider_id = provider_spec.provider_id
        if provider_id in self._providers:
            raise ValueError(f"named service provider already registered: {provider_id}")
        entry = RegisteredNamedServiceProvider(spec=provider_spec, provider=provider)
        self._providers[provider_id] = entry
        for namespace in provider_spec.namespaces or ():
            self._namespace_index.setdefault(namespace, []).append(provider_id)
        return entry

    def get(self, provider_id: str) -> RegisteredNamedServiceProvider | None:
        return self._providers.get(str(provider_id or "").strip())

    def providers(self) -> list[RegisteredNamedServiceProvider]:
        return list(self._providers.values())

    def resolve(
        self,
        *,
        provider_id: str | None = None,
        namespace: str | None = None,
        object_ref: str | None = None,
    ) -> RegisteredNamedServiceProvider | None:
        if provider_id:
            return self.get(provider_id)
        if object_ref:
            return self.resolve_object_ref(object_ref)
        if namespace:
            return self.resolve_namespace(namespace)
        return None

    def resolve_namespace(self, namespace: str) -> RegisteredNamedServiceProvider | None:
        ns = str(namespace or "").strip().lower()
        provider_ids = self._namespace_index.get(ns) or []
        if not provider_ids:
            return None
        if len(provider_ids) == 1:
            return self._providers[provider_ids[0]]
        raise ValueError(f"multiple named service providers registered for namespace {ns!r}; use provider_id")

    def resolve_object_ref(self, object_ref: str) -> RegisteredNamedServiceProvider | None:
        ref = str(object_ref or "").strip()
        if not ref:
            return None
        namespace = namespace_for_ref(ref)
        candidates = [
            self._providers[provider_id]
            for provider_id in self._namespace_index.get(namespace, [])
            if self._providers[provider_id].spec.matches_ref(ref)
        ]
        if not candidates:
            return self.resolve_namespace(namespace) if namespace else None
        candidates.sort(key=lambda entry: entry.spec.match_score(ref), reverse=True)
        if len(candidates) > 1:
            top_score = candidates[0].spec.match_score(ref)
            if candidates[1].spec.match_score(ref) == top_score:
                raise ValueError(f"multiple named service providers match object_ref {ref!r}; use provider_id")
        return candidates[0]
