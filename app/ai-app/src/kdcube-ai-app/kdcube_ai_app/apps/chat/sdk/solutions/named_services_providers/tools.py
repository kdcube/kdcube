# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any, Dict, Mapping

from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id

from .client_tools import (
    named_service_namespace_client_tools_config,
    named_service_namespace_provider_config,
    named_service_namespaces,
)
from .transports.api_client import NamedServiceApiEndpoint, call_named_service_api_endpoint
from .types import (
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
    NamedServiceRequest,
)


REGISTRY: Dict[str, Any] = {}
LOGGER = logging.getLogger("kdcube.sdk.named_services.tools")

_DEFAULT_READ_OPERATIONS = frozenset({
    PROVIDER_ABOUT,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_GET,
    OBJECT_ACTION,
})
_DEFAULT_ACTIONS = frozenset({"preview", "open", "capabilities", "describe"})


def bind_registry(registry: Mapping[str, Any] | None) -> None:
    global REGISTRY
    REGISTRY = dict(registry or {})


def _ok(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **dict(payload or {})}


def _error(code: str, message: str, **details: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": False, "error": code, "message": message}
    if details:
        payload["details"] = {k: v for k, v in details.items() if v not in (None, "")}
    return payload


def _client_id() -> str:
    explicit = REGISTRY.get("client_id")
    if explicit:
        return normalize_agent_id(explicit)
    comm_context = REGISTRY.get("comm_context")
    event = getattr(comm_context, "event", None) if comm_context is not None else None
    return normalize_agent_id(getattr(event, "agent_id", None), default=DEFAULT_REACT_AGENT_ID)


def _bundle_props() -> Mapping[str, Any]:
    props = REGISTRY.get("bundle_props")
    return props if isinstance(props, Mapping) else {}


def _json_object(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(f"{field_name} must be a JSON object") from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"{field_name} must be a JSON object")


def _json_list(value: Any, *, field_name: str) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(f"{field_name} must be a JSON list") from exc
        if isinstance(parsed, list):
            return list(parsed)
    raise ValueError(f"{field_name} must be a JSON list")


def _normalize_namespace(namespace: Any) -> str:
    return str(namespace or "").strip().lower().rstrip(":")


def _client_namespace_policy(namespace: str) -> Mapping[str, Any]:
    return named_service_namespace_client_tools_config(
        _bundle_props(),
        namespace=namespace,
        client_id=_client_id(),
    )


def _namespace_config(namespace: str) -> Mapping[str, Any]:
    raw = named_service_namespaces(_bundle_props()).get(namespace)
    return raw if isinstance(raw, Mapping) else {}


def _allowed_values(raw: Any, defaults: frozenset[str]) -> frozenset[str]:
    if raw in (None, ""):
        return defaults
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return frozenset()
    normalized = {str(item or "").strip() for item in values if str(item or "").strip()}
    if "*" in normalized:
        return frozenset({"*"})
    return frozenset(normalized)


def _operation_allowed(namespace: str, operation: str) -> bool:
    policy = _client_namespace_policy(namespace)
    allowed = _allowed_values(policy.get("operations"), _DEFAULT_READ_OPERATIONS)
    return "*" in allowed or operation in allowed


def _action_allowed(namespace: str, action: str) -> bool:
    if not action:
        return True
    policy = _client_namespace_policy(namespace)
    allowed = _allowed_values(policy.get("actions"), _DEFAULT_ACTIONS)
    return "*" in allowed or action in allowed


def _endpoint(namespace: str) -> NamedServiceApiEndpoint | Dict[str, Any]:
    namespace_cfg = _namespace_config(namespace)
    if not namespace_cfg:
        return _error(
            "named_service_namespace_not_configured",
            f"Namespace {namespace!r} is not configured under named_services.namespaces.",
            namespace=namespace,
        )
    policy = _client_namespace_policy(namespace)
    if not policy:
        return _error(
            "named_service_client_namespace_not_allowed",
            f"Client {_client_id()!r} is not configured to use namespace {namespace!r}.",
            namespace=namespace,
            client_id=_client_id(),
        )
    provider_cfg = named_service_namespace_provider_config(_bundle_props(), namespace=namespace)
    if not provider_cfg:
        return _error(
            "named_service_namespace_provider_missing",
            f"Namespace {namespace!r} is missing provider configuration.",
            namespace=namespace,
        )
    bundle_id = str(provider_cfg.get("bundle_id") or "").strip()
    if not bundle_id:
        return _error(
            "named_service_namespace_bundle_missing",
            f"Namespace {namespace!r} provider is missing bundle_id.",
            namespace=namespace,
        )
    return NamedServiceApiEndpoint(
        bundle_id=bundle_id,
        operation=str(provider_cfg.get("operation") or "named_service").strip() or "named_service",
        route=str(provider_cfg.get("route") or "operations").strip() or "operations",
        tenant=str(provider_cfg.get("tenant") or "").strip() or None,
        project=str(provider_cfg.get("project") or "").strip() or None,
        provider=str(provider_cfg.get("provider") or "").strip() or None,
        namespace=namespace,
    )


async def _call(
    *,
    namespace: str,
    operation: str,
    object_ref: str | None = None,
    object_id: str | None = None,
    query: str | None = None,
    action: str | None = None,
    collection: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    filters: Mapping[str, Any] | None = None,
    sort: list[Any] | None = None,
    include: list[Any] | None = None,
    object_payload: Mapping[str, Any] | None = None,
    base_revision: str | None = None,
    idempotency_key: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    ns = _normalize_namespace(namespace)
    if not ns:
        return _error("named_service_namespace_required", "namespace is required")
    if not _operation_allowed(ns, operation):
        return _error(
            "named_service_operation_not_allowed_for_client",
            f"Client {_client_id()!r} is not configured to call {operation} on namespace {ns!r}.",
            namespace=ns,
            operation=operation,
            client_id=_client_id(),
        )
    if action and not _action_allowed(ns, action):
        return _error(
            "named_service_action_not_allowed_for_client",
            f"Client {_client_id()!r} is not configured to call action {action!r} on namespace {ns!r}.",
            namespace=ns,
            action=action,
            client_id=_client_id(),
        )
    endpoint = _endpoint(ns)
    if isinstance(endpoint, dict):
        LOGGER.warning(
            "Named-service client endpoint unavailable: namespace=%s operation=%s client=%s error=%s",
            ns,
            operation,
            _client_id(),
            endpoint.get("error"),
        )
        return endpoint

    LOGGER.info(
        "Named-service client call start: namespace=%s operation=%s action=%s client=%s provider_bundle=%s provider=%s route=%s",
        ns,
        operation,
        action or "",
        _client_id(),
        endpoint.bundle_id,
        endpoint.provider or "",
        endpoint.route,
    )
    request = NamedServiceRequest(
        operation=operation,
        provider=endpoint.provider,
        namespace=ns,
        object_ref=str(object_ref or "").strip() or None,
        object_id=str(object_id or "").strip() or None,
        collection=str(collection or "").strip() or None,
        cursor=str(cursor or "").strip() or None,
        limit=limit,
        query=str(query or "").strip() or None,
        search_mode="hybrid" if operation == OBJECT_SEARCH else None,
        filters=dict(filters or {}),
        sort=list(sort or []),
        include=list(include or []),
        action=str(action or "").strip() or None,
        object=dict(object_payload or {}),
        base_revision=str(base_revision or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip() or None,
        context={
            "source": "named_services.client_tool",
            "client_id": _client_id(),
        },
        payload=dict(payload or {}),
    )
    response = await call_named_service_api_endpoint(endpoint, request)
    payload = response.to_dict()
    log_fn = LOGGER.info if payload.get("ok") else LOGGER.warning
    log_fn(
        "Named-service client call complete: namespace=%s operation=%s action=%s client=%s ok=%s error=%s status=%s",
        ns,
        operation,
        action or "",
        _client_id(),
        payload.get("ok"),
        (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), Mapping) else "",
        payload.get("status"),
    )
    return payload


async def provider_about(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
) -> Annotated[Dict[str, Any], "Named service response envelope."]:
    """Describe a configured named-service provider."""

    return await _call(namespace=namespace, operation=PROVIDER_ABOUT)


async def list_objects(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    collection: Annotated[str, "Optional provider collection, for example 'issues'."] = "",
    cursor: Annotated[str, "Optional pagination cursor from a previous response."] = "",
    limit: Annotated[int, "Maximum objects to return. Keep this bounded."] = 20,
    filters: Annotated[str, "Optional JSON object with provider-specific filters."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with items and next_cursor."]:
    """List objects from a configured named-service namespace."""

    try:
        parsed_filters = _json_object(filters, field_name="filters")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_LIST,
        collection=collection,
        cursor=cursor,
        limit=max(1, min(int(limit or 20), 100)),
        filters=parsed_filters,
    )


async def search_objects(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    query: Annotated[str, "Search query. Providers should use hybrid search when available."],
    limit: Annotated[int, "Maximum objects to return. Keep this bounded."] = 10,
    cursor: Annotated[str, "Optional pagination cursor from a previous response."] = "",
    filters: Annotated[str, "Optional JSON object with provider-specific filters."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with matching items."]:
    """Search objects in a configured named-service namespace."""

    try:
        parsed_filters = _json_object(filters, field_name="filters")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_SEARCH,
        query=query,
        cursor=cursor,
        limit=max(1, min(int(limit or 10), 50)),
        filters=parsed_filters,
    )


async def get_object(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    object_ref: Annotated[str, "Canonical object ref, for example 'task:issues/BUG-123'."] = "",
    object_id: Annotated[str, "Owner-local object id when object_ref is not known."] = "",
    include: Annotated[str, "Optional JSON list of extra fields or relations to include."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with object."]:
    """Read one object from a configured named-service namespace."""

    try:
        parsed_include = _json_list(include, field_name="include")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_GET,
        object_ref=object_ref,
        object_id=object_id,
        include=parsed_include,
    )


async def object_action(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    object_ref: Annotated[str, "Canonical object ref, for example 'task:issues/BUG-123'."],
    action: Annotated[str, "Bounded provider action, for example preview, open, or describe."] = "preview",
    payload: Annotated[str, "Optional JSON object with provider-specific action payload."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with object, data, or ui_event."]:
    """Run a bounded action against one object in a configured namespace."""

    try:
        parsed_payload = _json_object(payload, field_name="payload")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_ACTION,
        object_ref=object_ref,
        action=action or "preview",
        payload=parsed_payload,
    )


async def upsert_object(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    object_json: Annotated[str, "JSON object to create or update."],
    object_ref: Annotated[str, "Canonical object ref when updating an existing object."] = "",
    object_id: Annotated[str, "Owner-local object id when object_ref is not known."] = "",
    base_revision: Annotated[str, "Optional expected revision for optimistic concurrency."] = "",
    idempotency_key: Annotated[str, "Optional client operation id for idempotent creates/updates."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with object/revision."]:
    """Create or update an object when the client policy allows mutation."""

    try:
        parsed_object = _json_object(object_json, field_name="object_json")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_UPSERT,
        object_ref=object_ref,
        object_id=object_id,
        object_payload=parsed_object,
        base_revision=base_revision,
        idempotency_key=idempotency_key,
    )


async def delete_object(
    namespace: Annotated[str, "Configured named-service namespace, for example 'task'."],
    object_ref: Annotated[str, "Canonical object ref to delete or archive."],
    base_revision: Annotated[str, "Optional expected revision for optimistic concurrency."] = "",
    payload: Annotated[str, "Optional JSON object with provider-specific delete/archive options."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope."]:
    """Delete or archive an object when the client policy allows mutation."""

    try:
        parsed_payload = _json_object(payload, field_name="payload")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        operation=OBJECT_DELETE,
        object_ref=object_ref,
        base_revision=base_revision,
        payload=parsed_payload,
    )


def list_tools() -> Dict[str, Dict[str, Any]]:
    return {
        "provider_about": {
            "callable": provider_about,
            "description": "Describe a configured named-service provider available to this client.",
        },
        "list_objects": {
            "callable": list_objects,
            "description": "List objects from a configured named-service namespace with pagination.",
        },
        "search_objects": {
            "callable": search_objects,
            "description": "Search objects from a configured named-service namespace with cursor pagination. Uses provider hybrid search when available.",
        },
        "get_object": {
            "callable": get_object,
            "description": "Read one object from a configured named-service namespace by object_ref or object_id.",
        },
        "object_action": {
            "callable": object_action,
            "description": "Run a bounded provider action such as preview, open, or describe on one named-service object.",
        },
        "upsert_object": {
            "callable": upsert_object,
            "description": "Create or update one named-service object when this client's policy allows mutation.",
        },
        "delete_object": {
            "callable": delete_object,
            "description": "Delete or archive one named-service object when this client's policy allows mutation.",
        },
    }


tools = sys.modules[__name__]
