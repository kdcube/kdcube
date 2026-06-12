# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    block_production_policy,
    discover_react_event_policies,
)

from .client_tools import named_service_namespace_provider_configs_from_config
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from .types import BLOCK_PRODUCE, NamedServiceRequest


NAMED_SERVICE_EVENT_SOURCE_PREFIX = "named_services."
NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID = "named_services.block_production.provider"


def named_service_event_source_id(namespace: str) -> str:
    return f"{NAMED_SERVICE_EVENT_SOURCE_PREFIX}{str(namespace or '').strip().lower().rstrip(':')}"


def _object_ref_from_target(target: Mapping[str, Any]) -> str:
    for value in (
        target.get("object_ref"),
        target.get("logical_path"),
        target.get("hosted_uri"),
    ):
        text = str(value or "").strip()
        if ":" in text:
            return text
    event = target.get("event") if isinstance(target.get("event"), Mapping) else {}
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    nested_event = payload.get("event") if isinstance(payload.get("event"), Mapping) else {}
    for source in (event, payload, nested_event):
        for key in ("object_ref", "ref", "path", "logical_path", "hosted_uri"):
            text = str(source.get(key) or "").strip()
            if ":" in text:
                return text
    meta = target.get("meta") if isinstance(target.get("meta"), Mapping) else {}
    meta_target = meta.get("target") if isinstance(meta.get("target"), Mapping) else {}
    for source in (meta, meta_target):
        for key in ("object_ref", "ref", "path", "logical_path", "hosted_uri"):
            text = str(source.get(key) or "").strip()
            if ":" in text:
                return text
    return ""


@block_production_policy(event_policy_id=NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID)
async def named_service_provider_block_production_policy(
    target: MutableMapping[str, Any],
    *,
    namespace: str = "",
    provider_config: Mapping[str, Any] | None = None,
    **_: Any,
) -> MutableMapping[str, Any]:
    """Ask a configured named-service provider to produce ReAct blocks."""
    if not isinstance(target, MutableMapping):
        return target
    ns = str(namespace or "").strip().lower().rstrip(":")
    if not ns:
        return target
    object_ref = _object_ref_from_target(target)
    if not object_ref or not object_ref.startswith(f"{ns}:"):
        return target
    provider_configs = named_service_namespace_provider_configs_from_config(provider_config or {})
    endpoint = (
        NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=ns)
        if provider_configs
        else NamedServiceEndpoint(namespace=ns)
    )
    response = await call_named_service_endpoint(
        endpoint,
        NamedServiceRequest(
            operation=BLOCK_PRODUCE,
            provider=endpoint.provider,
            namespace=ns,
            object_ref=object_ref,
            context={"source": "named_services.block_policy"},
            payload={
                "target": dict(target),
                "event": dict(target.get("event") or {}) if isinstance(target.get("event"), Mapping) else {},
            },
        ),
    )
    if not response.ok:
        return target
    response_extra = response.extra if isinstance(response.extra, Mapping) else {}
    produced = response_extra.get("blocks")
    if not isinstance(produced, list):
        return target
    blocks = target.setdefault("blocks", [])
    if not isinstance(blocks, list):
        return target
    for block in produced:
        if isinstance(block, Mapping):
            blocks.append(dict(block))
    if produced:
        target["blocks_produced"] = True
    return target


def register_configured_named_service_event_sources(
    subsystem: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    logger: Any = None,
) -> None:
    register = getattr(subsystem, "register_event_source", None)
    if not callable(register):
        return
    event_policies = discover_react_event_policies(sys.modules[__name__])
    for namespace, config in sorted((namespaces or {}).items(), key=lambda item: str(item[0])):
        ns = str(namespace or "").strip().lower().rstrip(":")
        if not ns or not isinstance(config, Mapping):
            continue
        provider_configs = named_service_namespace_provider_configs_from_config(config)
        event_source_id = named_service_event_source_id(ns)
        register(
            event_source_id,
            policies=[
                {
                    "react_phase": "block_production",
                    "event_policy_id": NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID,
                    "params": {
                        "namespace": ns,
                        "provider_config": {"providers": list(provider_configs)},
                    },
                }
            ],
            description=f"Named-service namespace event source for {ns}.",
            kind="named_service",
            module=__name__,
            alias="named_services",
            object_name=event_source_id,
            event_policies=event_policies,
        )
        if logger is not None:
            try:
                logger.info("Registered named-service event source: namespace=%s event_source_id=%s", ns, event_source_id)
            except Exception:
                pass


__all__ = [
    "NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID",
    "NAMED_SERVICE_EVENT_SOURCE_PREFIX",
    "named_service_event_source_id",
    "named_service_provider_block_production_policy",
    "register_configured_named_service_event_sources",
]
