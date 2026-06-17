# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    block_production_policy,
    discover_react_event_policies,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import block_event_source_id

from .client_tools import named_service_namespace_provider_configs_from_config
from .discovery import ConfiguredNamedServiceDiscovery, get_current_named_service_discovery
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from .types import BLOCK_PRODUCE, BLOCK_RENDER, EVENT_RESOLVE, NamedServiceRequest


NAMED_SERVICE_EVENT_SOURCE_PREFIX = "named_services."
NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID = "named_services.block_production.provider"

LOGGER = logging.getLogger(__name__)
_BLOCK_RENDER_UNSUPPORTED: set[tuple[str, str]] = set()


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


def _namespace_from_ref(ref: str) -> str:
    text = str(ref or "").strip()
    if ":" not in text:
        return ""
    return text.split(":", 1)[0].strip().lower().rstrip(":")


def _source_provider_configs(source: Any, namespace: str) -> list[Mapping[str, Any]]:
    for spec in getattr(source, "policies", ()) or ():
        if not isinstance(spec, Mapping):
            continue
        if str(spec.get("event_policy_id") or "").strip() != NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID:
            continue
        params = spec.get("params") if isinstance(spec.get("params"), Mapping) else {}
        if str(params.get("namespace") or namespace).strip().lower().rstrip(":") != namespace:
            continue
        provider_config = params.get("provider_config") if isinstance(params.get("provider_config"), Mapping) else {}
        return named_service_namespace_provider_configs_from_config(provider_config)
    return []


def _block_owned_by_named_service(
    block: Mapping[str, Any],
    *,
    namespace: str,
    event_source_id: str,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    if block_event_source_id(block, call_meta=call_meta) == event_source_id:
        return True
    object_ref = _object_ref_from_target(block)
    return bool(object_ref and _namespace_from_ref(object_ref) == namespace)


def _render_window_blocks(
    timeline_blocks: list[MutableMapping[str, Any]],
    *,
    owned_indexes: set[int],
    neighbor_radius: int,
    max_blocks: int,
) -> list[dict[str, Any]]:
    selected: set[int] = {idx for idx in owned_indexes if 0 <= idx < len(timeline_blocks)}
    radius = max(0, int(neighbor_radius or 0))
    max_count = max(1, int(max_blocks or 1))
    for distance in range(1, radius + 1):
        if len(selected) >= max_count and len(selected) >= len(owned_indexes):
            break
        for index in sorted(owned_indexes):
            for candidate in (index - distance, index + distance):
                if 0 <= candidate < len(timeline_blocks):
                    selected.add(candidate)
                if len(selected) >= max_count and len(selected) >= len(owned_indexes):
                    break
    out: list[dict[str, Any]] = []
    for index in sorted(selected):
        block = copy.deepcopy(timeline_blocks[index])
        if isinstance(block, MutableMapping):
            block["index"] = index
            out.append(dict(block))
    return out


def _coerce_int(value: Any) -> int | None:
    try:
        index = int(value)
    except Exception:
        return None
    return index if index >= 0 else None


def _prepared_render_block(
    block: Mapping[str, Any],
    *,
    namespace: str,
    event_source_id: str,
    fallback_object_ref: str = "",
) -> dict[str, Any] | None:
    out = copy.deepcopy(dict(block or {}))
    out.pop("index", None)
    object_ref = _object_ref_from_target(out) or fallback_object_ref
    if object_ref and _namespace_from_ref(object_ref) != namespace:
        return None
    if object_ref:
        out.setdefault("object_ref", object_ref)
    out.setdefault("event_source_id", event_source_id)
    meta = out.get("meta") if isinstance(out.get("meta"), Mapping) else {}
    meta = dict(meta or {})
    if object_ref:
        meta.setdefault("object_ref", object_ref)
        meta.setdefault("source_namespace", namespace)
    meta.setdefault("resolved_event_source_id", event_source_id)
    meta["provider_rendered"] = True
    out["meta"] = meta
    return out


def _normalise_render_patches(
    response: Any,
    *,
    namespace: str,
    event_source_id: str,
    owned_indexes: set[int],
    timeline_blocks: list[MutableMapping[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    extra = response.extra if hasattr(response, "extra") else {}
    ret = response.ret if hasattr(response, "ret") and isinstance(response.ret, Mapping) else {}
    patches_raw = extra.get("patches") if isinstance(extra, Mapping) else None
    if patches_raw is None and isinstance(ret, Mapping):
        patches_raw = ret.get("patches")
    blocks_raw = extra.get("blocks") if isinstance(extra, Mapping) else None
    if blocks_raw is None and isinstance(ret, Mapping):
        blocks_raw = ret.get("blocks")

    raw_items: list[Any] = []
    if isinstance(patches_raw, list):
        raw_items.extend(patches_raw)
    if isinstance(blocks_raw, list):
        for block in blocks_raw:
            if not isinstance(block, Mapping):
                continue
            index = _coerce_int(block.get("index") or block.get("target_index") or block.get("block_index"))
            if index is not None:
                raw_items.append({"op": "replace_block", "index": index, "block": dict(block)})

    patches: list[dict[str, Any]] = []
    rejected = 0
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            rejected += 1
            continue
        op = str(raw.get("op") or raw.get("operation") or "").strip().lower().replace("-", "_")
        if op in {"replace", "replace_block", "set_block"}:
            index = _coerce_int(raw.get("index") or raw.get("target_index") or raw.get("block_index"))
            block = raw.get("block") if isinstance(raw.get("block"), Mapping) else {}
            if index is None or index not in owned_indexes or not isinstance(block, Mapping):
                rejected += 1
                continue
            fallback_ref = _object_ref_from_target(timeline_blocks[index]) if index < len(timeline_blocks) else ""
            prepared = _prepared_render_block(
                block,
                namespace=namespace,
                event_source_id=event_source_id,
                fallback_object_ref=fallback_ref,
            )
            if prepared is None:
                rejected += 1
                continue
            patches.append({"op": "replace_block", "index": index, "block": prepared})
            continue
        if op in {"patch", "patch_block", "update", "update_block"}:
            index = _coerce_int(raw.get("index") or raw.get("target_index") or raw.get("block_index"))
            fields = raw.get("fields") if isinstance(raw.get("fields"), Mapping) else raw.get("patch")
            if index is None or index not in owned_indexes or not isinstance(fields, Mapping):
                rejected += 1
                continue
            original = copy.deepcopy(dict(timeline_blocks[index]))
            for key, value in dict(fields).items():
                if key == "meta" and isinstance(value, Mapping):
                    merged_meta = dict(original.get("meta") if isinstance(original.get("meta"), Mapping) else {})
                    merged_meta.update(dict(value))
                    original["meta"] = merged_meta
                else:
                    original[key] = value
            prepared = _prepared_render_block(
                original,
                namespace=namespace,
                event_source_id=event_source_id,
                fallback_object_ref=_object_ref_from_target(timeline_blocks[index]),
            )
            if prepared is None:
                rejected += 1
                continue
            patches.append({"op": "replace_block", "index": index, "block": prepared})
            continue
        if op in {"append", "append_block", "append_block_after"}:
            index = _coerce_int(raw.get("index") or raw.get("anchor_index") or raw.get("after_index"))
            block = raw.get("block") if isinstance(raw.get("block"), Mapping) else {}
            if index is None or index not in owned_indexes or not isinstance(block, Mapping):
                rejected += 1
                continue
            prepared = _prepared_render_block(
                block,
                namespace=namespace,
                event_source_id=event_source_id,
                fallback_object_ref=_object_ref_from_target(timeline_blocks[index]),
            )
            if prepared is None:
                rejected += 1
                continue
            patches.append({"op": "append_block_after", "index": index, "block": prepared})
            continue
        rejected += 1
    status = "patches" if patches else "empty"
    if rejected:
        status = f"{status};rejected={rejected}"
    return patches, status


async def _call_named_service_block_render(
    *,
    namespace: str,
    event_source_id: str,
    source: Any,
    timeline_blocks: list[MutableMapping[str, Any]],
    owned_indexes: set[int],
    call_meta: Mapping[str, Mapping[str, Any]] | None,
    react_phase: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    unsupported_key = (namespace, event_source_id)
    if unsupported_key in _BLOCK_RENDER_UNSUPPORTED:
        LOGGER.debug(
            "[named_services.block_render] status=not_declared_cached namespace=%s event_source_id=%s",
            namespace,
            event_source_id,
        )
        return {"namespace": namespace, "event_source_id": event_source_id, "patches": [], "status": "not_declared_cached"}
    provider_configs = _source_provider_configs(source, namespace)
    endpoint = (
        NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=namespace)
        if provider_configs
        else NamedServiceEndpoint(namespace=namespace)
    )
    max_blocks = _coerce_int(context.get("named_service_render_max_blocks")) or 64
    neighbor_radius = _coerce_int(context.get("named_service_render_neighbor_radius")) or 4
    blocks = _render_window_blocks(
        timeline_blocks,
        owned_indexes=owned_indexes,
        neighbor_radius=neighbor_radius,
        max_blocks=max_blocks,
    )
    trigger_refs = sorted({
        ref
        for idx in owned_indexes
        for ref in (_object_ref_from_target(timeline_blocks[idx]),)
        if ref and _namespace_from_ref(ref) == namespace
    })
    LOGGER.info(
        "[named_services.block_render] status=called namespace=%s event_source_id=%s owned=%s window_blocks=%s",
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
                    "source": "named_services.block_render_policy",
                    "react_phase": react_phase,
                    "event_source_id": event_source_id,
                },
                payload={
                    "blocks": blocks,
                    "render_context": {
                        "phase": react_phase,
                        "audience": "model",
                        "event_source_id": event_source_id,
                        "trigger_object_refs": trigger_refs,
                        "limits": {
                            "max_blocks": max_blocks,
                            "neighbor_radius": neighbor_radius,
                        },
                    },
                },
            ),
        )
    except Exception as exc:
        LOGGER.warning(
            "[named_services.block_render] status=error namespace=%s event_source_id=%s error=%s",
            namespace,
            event_source_id,
            type(exc).__name__,
            exc_info=True,
        )
        return {"namespace": namespace, "event_source_id": event_source_id, "patches": [], "status": "error"}
    if not getattr(response, "ok", False):
        code = str(getattr(getattr(response, "error", None), "code", "") or "").strip()
        status = "not_declared" if code == "named_service_operation_not_supported" else "error"
        if status == "not_declared":
            _BLOCK_RENDER_UNSUPPORTED.add(unsupported_key)
        LOGGER.info(
            "[named_services.block_render] status=%s namespace=%s event_source_id=%s code=%s",
            status,
            namespace,
            event_source_id,
            code,
        )
        return {"namespace": namespace, "event_source_id": event_source_id, "patches": [], "status": status}
    patches, status = _normalise_render_patches(
        response,
        namespace=namespace,
        event_source_id=event_source_id,
        owned_indexes=owned_indexes,
        timeline_blocks=timeline_blocks,
    )
    LOGGER.info(
        "[named_services.block_render] status=%s namespace=%s event_source_id=%s patches=%s",
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


def _apply_render_patch_results(
    timeline_blocks: list[MutableMapping[str, Any]],
    results: list[dict[str, Any]],
) -> int:
    replacements: dict[int, dict[str, Any]] = {}
    appends: dict[int, list[dict[str, Any]]] = {}
    changed = 0
    for result in results or []:
        for patch in result.get("patches") or []:
            if not isinstance(patch, Mapping):
                continue
            op = str(patch.get("op") or "").strip()
            index = _coerce_int(patch.get("index"))
            block = patch.get("block") if isinstance(patch.get("block"), Mapping) else None
            if index is None or block is None:
                continue
            if op == "replace_block":
                if index not in replacements:
                    replacements[index] = dict(block)
                    changed += 1
                continue
            if op == "append_block_after":
                appends.setdefault(index, []).append(dict(block))
                changed += 1
    for index, block in replacements.items():
        if 0 <= index < len(timeline_blocks):
            timeline_blocks[index] = block
    if appends:
        rebuilt: list[MutableMapping[str, Any]] = []
        for index, block in enumerate(timeline_blocks):
            rebuilt.append(block)
            for appended in appends.get(index, ()):
                rebuilt.append(appended)
        timeline_blocks[:] = rebuilt
    return changed


async def apply_named_service_block_render_projection(
    *,
    event_sources: Any,
    timeline_blocks: list[MutableMapping[str, Any]],
    react_phase: str = "timeline_projection",
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Apply named-service provider render hooks in parallel and merge safely."""
    if event_sources is None or not timeline_blocks:
        return timeline_blocks
    call_meta = context.get("call_meta") if isinstance(context.get("call_meta"), Mapping) else None
    grouped: dict[str, dict[str, Any]] = {}
    for index, block in enumerate(timeline_blocks or []):
        if not isinstance(block, Mapping):
            continue
        object_ref = _object_ref_from_target(block)
        namespace = _namespace_from_ref(object_ref)
        event_source_id = block_event_source_id(block, call_meta=call_meta)
        if not event_source_id and namespace:
            event_source_id = named_service_event_source_id(namespace)
        if not event_source_id.startswith(NAMED_SERVICE_EVENT_SOURCE_PREFIX):
            continue
        if not namespace:
            namespace = event_source_id.removeprefix(NAMED_SERVICE_EVENT_SOURCE_PREFIX).split(".", 1)[0].strip().lower().rstrip(":")
        if not namespace:
            continue
        source = getattr(event_sources, "by_event_source_id", lambda _value: None)(event_source_id)
        if source is None:
            continue
        if not _block_owned_by_named_service(
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
    if not grouped:
        return timeline_blocks

    tasks = [
        _call_named_service_block_render(
            namespace=str(item["namespace"]),
            event_source_id=str(item["event_source_id"]),
            source=item["source"],
            timeline_blocks=timeline_blocks,
            owned_indexes=set(item["owned_indexes"]),
            call_meta=call_meta,
            react_phase=react_phase,
            context=context,
        )
        for item in grouped.values()
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    for result in raw_results:
        if isinstance(result, Exception):
            LOGGER.warning(
                "[named_services.block_render] status=error error=%s",
                type(result).__name__,
                exc_info=(type(result), result, result.__traceback__),
            )
            continue
        if isinstance(result, Mapping):
            results.append(dict(result))
    changed = _apply_render_patch_results(timeline_blocks, results)
    if changed:
        LOGGER.info("[named_services.block_render] status=merged providers=%s changed=%s", len(results), changed)
    return timeline_blocks


async def _resolve_named_service_event_source(
    *,
    namespace: str,
    provider_configs: list[Mapping[str, Any]],
    ref: str,
) -> dict[str, Any]:
    ns = str(namespace or "").strip().lower().rstrip(":")
    object_ref = str(ref or "").strip()
    if not ns or not object_ref:
        return {"ok": False, "ref": object_ref, "missing": True, "error": "missing_namespace_or_ref"}
    request = NamedServiceRequest(
        operation=EVENT_RESOLVE,
        namespace=ns,
        object_ref=object_ref,
        context={"source": "event_source_resolver"},
        payload={"source": "event_source_resolver"},
    )
    if provider_configs:
        discovery = ConfiguredNamedServiceDiscovery(provider_configs, namespace=ns)
    else:
        discovery = get_current_named_service_discovery()
    if discovery is None:
        return {"ok": False, "ref": object_ref, "namespace": ns, "missing": True, "error": "no_named_service_discovery"}
    entry = await discovery.resolve(request, namespace=ns)
    if entry is None:
        return {"ok": False, "ref": object_ref, "namespace": ns, "missing": True, "error": "no_matching_named_service_provider"}
    endpoint = NamedServiceEndpoint.from_provider_config(
        {
            **dict(entry.endpoint or {}),
            "bundle_id": entry.spec.bundle_id or (entry.endpoint or {}).get("bundle_id"),
            "provider": entry.spec.provider_id,
        },
        namespace=ns,
    )
    response = await call_named_service_endpoint(endpoint, request)
    if not response.ok:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": ns,
            "provider_id": entry.spec.provider_id,
            "bundle_id": entry.spec.bundle_id,
            "error": response.error.code if response.error else "event_resolve_failed",
            "message": response.error.message if response.error else "Named-service event resolver failed",
        }
    extra = response.extra if isinstance(response.extra, Mapping) else {}
    attrs = response.attrs if isinstance(response.attrs, Mapping) else {}
    event_source_id = str(extra.get("event_source_id") or attrs.get("event_source_id") or response.ret.get("event_source_id") or "").strip()
    if not event_source_id:
        return {
            "ok": False,
            "ref": object_ref,
            "namespace": ns,
            "provider_id": entry.spec.provider_id,
            "bundle_id": entry.spec.bundle_id,
            "missing": True,
            "error": "event_resolver_returned_no_event_source_id",
        }
    return {
        "ok": True,
        "ref": object_ref,
        "object_ref": object_ref,
        "namespace": ns,
        "event_source_id": event_source_id,
        "provider_id": entry.spec.provider_id,
        "bundle_id": entry.spec.bundle_id,
        "extra": dict(extra),
    }


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
    register_resolver = getattr(subsystem, "register_event_source_resolver", None)
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
        if callable(register_resolver):
            register_resolver(
                ns,
                lambda ref, namespace=ns, provider_configs=tuple(provider_configs), **__: _resolve_named_service_event_source(
                    namespace=namespace,
                    provider_configs=[dict(item) for item in provider_configs],
                    ref=ref,
                ),
                description=f"Named-service provider URI resolver for {ns}.",
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
