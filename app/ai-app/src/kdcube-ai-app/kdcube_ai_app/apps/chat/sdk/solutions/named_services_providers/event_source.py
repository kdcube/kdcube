# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Named-service event-source resolution and block production."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.runtime.harness.timeline import object_ref_from_block
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
)

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import named_service_namespace_provider_configs_from_config
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
    ConfiguredNamedServiceDiscovery,
    get_current_named_service_discovery,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import BLOCK_PRODUCE, EVENT_RESOLVE, NamedServiceRequest


NAMED_SERVICE_EVENT_SOURCE_PREFIX = "named_services."
NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID = "named_services.block_production.provider"


def named_service_event_source_id(namespace: str) -> str:
    namespace = str(namespace or "").strip().lower().rstrip(":")
    return f"{NAMED_SERVICE_EVENT_SOURCE_PREFIX}{namespace}"


async def resolve_named_service_event_source(
    *,
    namespace: str,
    provider_configs: list[Mapping[str, Any]],
    ref: str,
) -> dict[str, Any]:
    """Ask the namespace owner which event source owns a canonical object ref."""
    namespace = str(namespace or "").strip().lower().rstrip(":")
    object_ref = str(ref or "").strip()
    if not namespace or not object_ref:
        return {
            "ok": False,
            "ref": object_ref,
            "missing": True,
            "error": "missing_namespace_or_ref",
        }
    request = NamedServiceRequest(
        operation=EVENT_RESOLVE,
        namespace=namespace,
        object_ref=object_ref,
        context={"source": "runtime.harness.events"},
        payload={"source": "runtime.harness.events"},
    )
    discovery = (
        ConfiguredNamedServiceDiscovery(provider_configs, namespace=namespace)
        if provider_configs
        else get_current_named_service_discovery()
    )
    if discovery is None:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": namespace,
            "missing": True,
            "error": "no_named_service_discovery",
        }
    entry = await discovery.resolve(request, namespace=namespace)
    if entry is None:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": namespace,
            "missing": True,
            "error": "no_matching_named_service_provider",
        }
    endpoint = NamedServiceEndpoint.from_provider_config(
        {
            **dict(entry.endpoint or {}),
            "bundle_id": entry.spec.bundle_id
            or (entry.endpoint or {}).get("bundle_id"),
            "provider": entry.spec.provider_id,
        },
        namespace=namespace,
    )
    response = await call_named_service_endpoint(
        endpoint,
        request,
        admission=NamedServiceAdmission.application(
            source="runtime.harness.events"
        ),
    )
    if not response.ok:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": namespace,
            "provider_id": entry.spec.provider_id,
            "bundle_id": entry.spec.bundle_id,
            "error": (
                response.error.code if response.error else "event_resolve_failed"
            ),
            "message": (
                response.error.message
                if response.error
                else "Named-service event resolver failed"
            ),
        }
    extra = response.extra if isinstance(response.extra, Mapping) else {}
    attrs = response.attrs if isinstance(response.attrs, Mapping) else {}
    ret = response.ret if isinstance(response.ret, Mapping) else {}
    event_source_id = str(
        extra.get("event_source_id")
        or attrs.get("event_source_id")
        or ret.get("event_source_id")
        or ""
    ).strip()
    if not event_source_id:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": namespace,
            "provider_id": entry.spec.provider_id,
            "bundle_id": entry.spec.bundle_id,
            "missing": True,
            "error": "event_resolver_returned_no_event_source_id",
        }
    return {
        "ok": True,
        "ref": object_ref,
        "object_ref": object_ref,
        "namespace": namespace,
        "event_source_id": event_source_id,
        "provider_id": entry.spec.provider_id,
        "bundle_id": entry.spec.bundle_id,
        "extra": dict(extra),
    }


async def produce_named_service_blocks(
    target: Mapping[str, Any],
    *,
    namespace: str,
    provider_config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ask the namespace owner for durable blocks representing one event/object."""
    namespace = str(namespace or "").strip().lower().rstrip(":")
    if not namespace:
        return []
    object_ref = object_ref_from_block(target)
    if not object_ref or not object_ref.startswith(f"{namespace}:"):
        return []
    provider_configs = named_service_namespace_provider_configs_from_config(
        provider_config or {}
    )
    endpoint = (
        NamedServiceEndpoint.from_provider_configs(
            provider_configs,
            namespace=namespace,
        )
        if provider_configs
        else NamedServiceEndpoint(namespace=namespace)
    )
    response = await call_named_service_endpoint(
        endpoint,
        NamedServiceRequest(
            operation=BLOCK_PRODUCE,
            provider=endpoint.provider,
            namespace=namespace,
            object_ref=object_ref,
            context={"source": "runtime.harness.events.block_produce"},
            payload={
                "target": dict(target),
                "event": (
                    dict(target.get("event") or {})
                    if isinstance(target.get("event"), Mapping)
                    else {}
                ),
            },
        ),
        admission=NamedServiceAdmission.application(
            source="runtime.harness.events.block_produce"
        ),
    )
    if not response.ok:
        return []
    extra = response.extra if isinstance(response.extra, Mapping) else {}
    produced = extra.get("blocks")
    if not isinstance(produced, list):
        return []
    return [dict(block) for block in produced if isinstance(block, Mapping)]


__all__ = [
    "NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID",
    "NAMED_SERVICE_EVENT_SOURCE_PREFIX",
    "named_service_event_source_id",
    "produce_named_service_blocks",
    "resolve_named_service_event_source",
]
