# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""ReAct policy binding for named-service event sources.

Provider calls and harness mechanics live outside this adapter. This module
only binds them to ReAct's ``block_production`` phase and registers configured
namespace event sources with the event subsystem.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    block_production_policy,
    discover_react_event_policies,
)

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import named_service_namespace_provider_configs_from_config
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.event_source import (
    NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID,
    NAMED_SERVICE_EVENT_SOURCE_PREFIX,
    named_service_event_source_id,
    produce_named_service_blocks,
    resolve_named_service_event_source,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.timeline_projection import apply_named_service_block_render_projection


@block_production_policy(event_policy_id=NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID)
async def named_service_provider_block_production_policy(
    target: MutableMapping[str, Any],
    *,
    namespace: str = "",
    provider_config: Mapping[str, Any] | None = None,
    **_: Any,
) -> MutableMapping[str, Any]:
    """Bind provider ``block.produce`` to a ReAct block-production target."""
    if not isinstance(target, MutableMapping):
        return target
    produced = await produce_named_service_blocks(
        target,
        namespace=namespace,
        provider_config=provider_config,
    )
    blocks = target.setdefault("blocks", [])
    if not isinstance(blocks, list):
        return target
    blocks.extend(produced)
    if produced:
        target["blocks_produced"] = True
    return target


def register_configured_named_service_event_sources(
    subsystem: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    logger: Any = None,
) -> None:
    """Register provider-backed event sources and owner-ref resolvers."""
    register = getattr(subsystem, "register_event_source", None)
    if not callable(register):
        return
    register_resolver = getattr(subsystem, "register_event_source_resolver", None)
    event_policies = discover_react_event_policies(sys.modules[__name__])
    for namespace, config in sorted(
        (namespaces or {}).items(),
        key=lambda item: str(item[0]),
    ):
        namespace = str(namespace or "").strip().lower().rstrip(":")
        if not namespace or not isinstance(config, Mapping):
            continue
        provider_configs = named_service_namespace_provider_configs_from_config(config)
        event_source_id = named_service_event_source_id(namespace)
        register(
            event_source_id,
            policies=[
                {
                    "react_phase": "block_production",
                    "event_policy_id": NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID,
                    "params": {
                        "namespace": namespace,
                        "provider_config": {"providers": list(provider_configs)},
                    },
                }
            ],
            description=f"Named-service namespace event source for {namespace}.",
            kind="named_service",
            module=__name__,
            alias="named_services",
            object_name=event_source_id,
            event_policies=event_policies,
        )
        if logger is not None:
            try:
                logger.info(
                    "Registered named-service event source: "
                    "namespace=%s event_source_id=%s",
                    namespace,
                    event_source_id,
                )
            except Exception:
                pass
        if callable(register_resolver):
            register_resolver(
                namespace,
                lambda ref, namespace=namespace, provider_configs=tuple(
                    provider_configs
                ), **__: resolve_named_service_event_source(
                    namespace=namespace,
                    provider_configs=[dict(item) for item in provider_configs],
                    ref=ref,
                ),
                description=f"Named-service provider URI resolver for {namespace}.",
                module=__name__,
                alias="named_services",
                object_name=f"{event_source_id}.resolver",
            )


__all__ = [
    "NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID",
    "NAMED_SERVICE_EVENT_SOURCE_PREFIX",
    "apply_named_service_block_render_projection",
    "named_service_event_source_id",
    "named_service_provider_block_production_policy",
    "register_configured_named_service_event_sources",
]
