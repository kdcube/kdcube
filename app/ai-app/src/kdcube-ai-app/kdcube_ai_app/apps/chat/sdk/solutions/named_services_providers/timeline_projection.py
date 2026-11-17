# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Named-service transport for harness timeline rendering."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.runtime.harness.timeline import (
    apply_provider_render_patch_results,
    block_event_source_id,
    block_owned_by_namespace,
    normalize_provider_render_patches,
    object_ref_from_block,
    render_window_blocks,
)
from kdcube_ai_app.apps.chat.sdk.runtime.harness.timeline.projection import (
    coerce_block_index,
    namespace_from_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
)

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import named_service_namespace_provider_configs_from_config
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.event_source import (
    NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID,
    NAMED_SERVICE_EVENT_SOURCE_PREFIX,
    named_service_event_source_id,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import BLOCK_RENDER, NamedServiceRequest


LOGGER = logging.getLogger(__name__)
_BLOCK_RENDER_UNSUPPORTED: set[tuple[str, str]] = set()


def _source_provider_configs(source: Any, namespace: str) -> list[Mapping[str, Any]]:
    """Read provider routing from the registered event-source declaration."""
    for spec in getattr(source, "policies", ()) or ():
        if not isinstance(spec, Mapping):
            continue
        if (
            str(spec.get("event_policy_id") or "").strip()
            != NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID
        ):
            continue
        params = spec.get("params") if isinstance(spec.get("params"), Mapping) else {}
        if (
            str(params.get("namespace") or namespace).strip().lower().rstrip(":")
            != namespace
        ):
            continue
        provider_config = (
            params.get("provider_config")
            if isinstance(params.get("provider_config"), Mapping)
            else {}
        )
        return named_service_namespace_provider_configs_from_config(provider_config)
    return []


async def _call_named_service_block_render(
    *,
    namespace: str,
    event_source_id: str,
    source: Any,
    timeline_blocks: list[MutableMapping[str, Any]],
    owned_indexes: set[int],
    phase: str,
    audience: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    unsupported_key = (namespace, event_source_id)
    if unsupported_key in _BLOCK_RENDER_UNSUPPORTED:
        LOGGER.debug(
            "[named_services.block_render] "
            "status=not_declared_cached namespace=%s event_source_id=%s",
            namespace,
            event_source_id,
        )
        return {
            "namespace": namespace,
            "event_source_id": event_source_id,
            "patches": [],
            "status": "not_declared_cached",
        }

    provider_configs = _source_provider_configs(source, namespace)
    endpoint = (
        NamedServiceEndpoint.from_provider_configs(
            provider_configs,
            namespace=namespace,
        )
        if provider_configs
        else NamedServiceEndpoint(namespace=namespace)
    )
    max_blocks = coerce_block_index(context.get("named_service_render_max_blocks")) or 64
    neighbor_radius = (
        coerce_block_index(context.get("named_service_render_neighbor_radius")) or 4
    )
    blocks = render_window_blocks(
        timeline_blocks,
        owned_indexes=owned_indexes,
        neighbor_radius=neighbor_radius,
        max_blocks=max_blocks,
    )
    trigger_refs = sorted(
        {
            ref
            for index in owned_indexes
            for ref in (object_ref_from_block(timeline_blocks[index]),)
            if ref and namespace_from_ref(ref) == namespace
        }
    )
    LOGGER.info(
        "[named_services.block_render] "
        "status=called namespace=%s event_source_id=%s owned=%s window_blocks=%s",
        namespace,
        event_source_id,
        len(owned_indexes),
        len(blocks),
    )
    try:
        response = await call_named_service_endpoint(
            endpoint,
            NamedServiceRequest(
                operation=BLOCK_RENDER,
                provider=endpoint.provider,
                namespace=namespace,
                object_ref=trigger_refs[0] if trigger_refs else None,
                context={
                    "source": "named_services.block_render",
                    "phase": phase,
                    "event_source_id": event_source_id,
                },
                payload={
                    "blocks": blocks,
                    "render_context": {
                        "phase": phase,
                        "audience": audience,
                        "event_source_id": event_source_id,
                        "trigger_object_refs": trigger_refs,
                        "limits": {
                            "max_blocks": max_blocks,
                            "neighbor_radius": neighbor_radius,
                        },
                    },
                },
            ),
            admission=NamedServiceAdmission.application(
                source="named_services.block_render"
            ),
        )
    except Exception as exc:
        LOGGER.warning(
            "[named_services.block_render] "
            "status=error namespace=%s event_source_id=%s error=%s",
            namespace,
            event_source_id,
            type(exc).__name__,
            exc_info=True,
        )
        return {
            "namespace": namespace,
            "event_source_id": event_source_id,
            "patches": [],
            "status": "error",
        }

    if not getattr(response, "ok", False):
        code = str(getattr(getattr(response, "error", None), "code", "") or "").strip()
        status = (
            "not_declared"
            if code == "named_service_operation_not_supported"
            else "error"
        )
        if status == "not_declared":
            _BLOCK_RENDER_UNSUPPORTED.add(unsupported_key)
        LOGGER.info(
            "[named_services.block_render] "
            "status=%s namespace=%s event_source_id=%s code=%s",
            status,
            namespace,
            event_source_id,
            code,
        )
        return {
            "namespace": namespace,
            "event_source_id": event_source_id,
            "patches": [],
            "status": status,
        }

    patches, status = normalize_provider_render_patches(
        response,
        namespace=namespace,
        event_source_id=event_source_id,
        owned_indexes=owned_indexes,
        timeline_blocks=timeline_blocks,
    )
    LOGGER.info(
        "[named_services.block_render] "
        "status=%s namespace=%s event_source_id=%s patches=%s",
        "rendered" if patches else "empty",
        namespace,
        event_source_id,
        len(patches),
    )
    return {
        "namespace": namespace,
        "event_source_id": event_source_id,
        "patches": patches,
        "status": status,
    }


async def apply_named_service_block_render_projection(
    *,
    event_sources: Any,
    timeline_blocks: list[MutableMapping[str, Any]],
    phase: str = "timeline_projection",
    audience: str = "model",
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Fan out provider rendering and merge only validated owned-block patches."""
    if event_sources is None or not timeline_blocks:
        return timeline_blocks
    call_meta = (
        context.get("call_meta")
        if isinstance(context.get("call_meta"), Mapping)
        else None
    )
    grouped: dict[str, dict[str, Any]] = {}
    for index, block in enumerate(timeline_blocks):
        if not isinstance(block, Mapping):
            continue
        object_ref = object_ref_from_block(block)
        namespace = namespace_from_ref(object_ref)
        event_source_id = block_event_source_id(block, call_meta=call_meta)
        if not event_source_id and namespace:
            event_source_id = named_service_event_source_id(namespace)
        if not event_source_id.startswith(NAMED_SERVICE_EVENT_SOURCE_PREFIX):
            continue
        if not namespace:
            namespace = (
                event_source_id.removeprefix(NAMED_SERVICE_EVENT_SOURCE_PREFIX)
                .split(".", 1)[0]
                .strip()
                .lower()
                .rstrip(":")
            )
        if not namespace:
            continue
        source = getattr(
            event_sources,
            "by_event_source_id",
            lambda _value: None,
        )(event_source_id)
        if source is None:
            continue
        if not block_owned_by_namespace(
            block,
            namespace=namespace,
            event_source_id=event_source_id,
            call_meta=call_meta,
        ):
            continue
        bucket = grouped.setdefault(
            event_source_id,
            {
                "namespace": namespace,
                "event_source_id": event_source_id,
                "source": source,
                "owned_indexes": set(),
            },
        )
        bucket["owned_indexes"].add(index)

    tasks = [
        _call_named_service_block_render(
            namespace=str(item["namespace"]),
            event_source_id=str(item["event_source_id"]),
            source=item["source"],
            timeline_blocks=timeline_blocks,
            owned_indexes=set(item["owned_indexes"]),
            phase=phase,
            audience=audience,
            context=context,
        )
        for item in grouped.values()
    ]
    if not tasks:
        return timeline_blocks

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[Mapping[str, Any]] = []
    for result in raw_results:
        if isinstance(result, Exception):
            LOGGER.warning(
                "[named_services.block_render] status=error error=%s",
                type(result).__name__,
                exc_info=(type(result), result, result.__traceback__),
            )
            continue
        if isinstance(result, Mapping):
            results.append(result)
    changed = apply_provider_render_patch_results(timeline_blocks, results)
    if changed:
        LOGGER.info(
            "[named_services.block_render] status=merged providers=%s changed=%s",
            len(results),
            changed,
        )
    return timeline_blocks


__all__ = ["apply_named_service_block_render_projection"]
