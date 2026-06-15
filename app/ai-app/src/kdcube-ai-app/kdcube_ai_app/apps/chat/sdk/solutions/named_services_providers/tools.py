# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import mimetypes
import pathlib
import sys
from typing import Annotated, Any, Dict, Mapping
from urllib.parse import unquote, urlparse

from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir, resolve_workdir
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_logical_artifact_path,
    split_physical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.named_service_search import NamedServiceSearchResultsWidget

from .client_tools import (
    named_service_namespace_client_tools_config,
    named_service_namespace_provider_configs,
    named_service_namespaces,
)
from .discovery import get_current_named_service_discovery
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from .types import (
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_HOST_FILE,
    OBJECT_LIST,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
    NamedServiceRequest,
    NamedServiceSearchScope,
    normalize_search_scopes,
    namespace_for_ref,
)


REGISTRY: Dict[str, Any] = {}
LOGGER = logging.getLogger("kdcube.sdk.named_services.tools")

_DEFAULT_READ_OPERATIONS = frozenset({
    PROVIDER_ABOUT,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_GET,
    OBJECT_SCHEMA,
})
_TOOL_OPERATIONS = {
    "provider_about": PROVIDER_ABOUT,
    "list_objects": OBJECT_LIST,
    "search_objects": OBJECT_SEARCH,
    "get_object": OBJECT_GET,
    "host_file": OBJECT_HOST_FILE,
    "object_schema": OBJECT_SCHEMA,
    "object_action": OBJECT_ACTION,
    "upsert_object": OBJECT_UPSERT,
    "delete_object": OBJECT_DELETE,
}


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


def _safe_filename(value: str, fallback: str = "attachment.bin") -> str:
    name = pathlib.PurePosixPath(str(value or "").replace("\\", "/")).name
    return name.strip() or fallback


def _is_nonlocal_ref(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or raw.startswith("file:"):
        return False
    scheme, sep, _ = raw.partition(":")
    if not sep:
        return False
    return scheme.isidentifier() and scheme.lower() == scheme


def _is_within(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


def _logical_ref_from_physical_artifact_path(value: str) -> str:
    turn_id, namespace, relpath = split_physical_artifact_path(value)
    if turn_id and namespace and relpath:
        return build_logical_artifact_path(turn_id=turn_id, namespace=namespace, relpath=relpath)
    return ""


def _runtime_roots() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for resolver in (resolve_output_dir, resolve_workdir):
        try:
            root = resolver()
        except Exception:
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _host_file_payload(
    *,
    file_ref: str,
    filename: str = "",
    mime: str = "",
    description: str = "",
    extra_payload: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    raw_ref = str(file_ref or "").strip()
    if not raw_ref:
        raise ValueError("file_ref is required")
    raw_filename = _safe_filename(filename or raw_ref)
    raw_mime = str(mime or "").strip()
    file_payload: Dict[str, Any] = {
        "ref": raw_ref,
        "filename": raw_filename,
        "mime": raw_mime,
        "description": str(description or "").strip(),
    }
    if _is_nonlocal_ref(raw_ref):
        return {"file": file_payload, **dict(extra_payload or {})}

    parsed = urlparse(raw_ref)
    if parsed.scheme == "file":
        candidate = pathlib.Path(unquote(parsed.path or ""))
    else:
        candidate = pathlib.Path(raw_ref)

    logical_ref = _logical_ref_from_physical_artifact_path(raw_ref)
    roots = _runtime_roots()
    if not roots:
        if logical_ref:
            file_payload["ref"] = logical_ref
            file_payload.setdefault("source", "artifact_ref")
            return {"file": file_payload, **dict(extra_payload or {})}
        raise ValueError("local file refs require a bound ReAct output/workdir context")

    candidates: list[pathlib.Path]
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        candidates = [root / candidate for root in roots]

    selected = next((path.resolve() for path in candidates if path.exists() and path.is_file()), None)
    if selected is None:
        if logical_ref:
            file_payload["ref"] = logical_ref
            file_payload.setdefault("source", "artifact_ref")
            return {"file": file_payload, **dict(extra_payload or {})}
        raise FileNotFoundError(f"local file ref was not found in the runtime output/workdir: {raw_ref}")
    if not _is_within(selected, roots):
        raise PermissionError("local file refs must resolve under the current ReAct output/workdir roots")

    detected_mime = raw_mime or mimetypes.guess_type(selected.name)[0] or "application/octet-stream"
    file_payload.update({
        "local_path": str(selected),
        "filename": _safe_filename(filename or selected.name),
        "mime": detected_mime,
        "size_bytes": selected.stat().st_size,
        "source": "local_path",
    })
    return {"file": file_payload, **dict(extra_payload or {})}


def _normalize_namespace(namespace: Any) -> str:
    return str(namespace or "").strip().lower().rstrip(":")


def _base_namespace(namespace: Any) -> str:
    ns = _normalize_namespace(namespace)
    return namespace_for_ref(ns) or ns


def _client_namespace_policy(namespace: str) -> Mapping[str, Any]:
    return named_service_namespace_client_tools_config(
        _bundle_props(),
        namespace=_base_namespace(namespace),
        client_id=_client_id(),
    )


def _namespace_config(namespace: str) -> Mapping[str, Any]:
    raw = named_service_namespaces(_bundle_props()).get(_base_namespace(namespace))
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
    allowed = _allowed_values(policy.get("allowed_operations"), _DEFAULT_READ_OPERATIONS)
    return "*" in allowed or operation in allowed


def _operation_applicable_namespaces(namespaces: list[str], operation: str) -> list[str]:
    applicable: list[str] = []
    for namespace in namespaces:
        ns = _normalize_namespace(namespace)
        if ns and _operation_allowed(ns, operation) and ns not in applicable:
            applicable.append(ns)
    return applicable


def _search_scope_to_dict(scope: NamedServiceSearchScope) -> Dict[str, Any]:
    return scope.to_dict()


def _add_search_scopes(
    target: Dict[str, list[Dict[str, Any]]],
    *,
    base_namespace: str,
    raw_scopes: Any,
) -> None:
    base_ns = _base_namespace(base_namespace)
    if not base_ns:
        return
    bucket = target.setdefault(base_ns, [])
    seen = {str(item.get("namespace") or "") for item in bucket if isinstance(item, Mapping)}
    for scope in normalize_search_scopes(raw_scopes, default_namespace=base_ns):
        if scope.namespace in seen:
            continue
        seen.add(scope.namespace)
        bucket.append(_search_scope_to_dict(scope))


def _snapshot_search_scopes() -> Dict[str, list[Dict[str, Any]]]:
    """Return provider-declared search scopes available without provider I/O."""

    out: Dict[str, list[Dict[str, Any]]] = {}
    registry_scopes = REGISTRY.get("named_service_search_scopes")
    if isinstance(registry_scopes, Mapping):
        for namespace, raw_scopes in registry_scopes.items():
            _add_search_scopes(out, base_namespace=str(namespace), raw_scopes=raw_scopes)

    for entry in REGISTRY.get("named_service_discovery_entries") or ():
        spec = getattr(entry, "spec", None)
        if spec is None and isinstance(entry, Mapping):
            spec = entry.get("spec") or entry
        namespaces = getattr(spec, "namespaces", None)
        search_scopes = getattr(spec, "search_scopes", None)
        if isinstance(spec, Mapping):
            namespaces = spec.get("namespaces") or ([spec.get("namespace")] if spec.get("namespace") else [])
            search_scopes = spec.get("search_scopes") or spec.get("searchScopes")
        for namespace in namespaces or ():
            _add_search_scopes(out, base_namespace=str(namespace), raw_scopes=search_scopes)

    for namespace, namespace_cfg in named_service_namespaces(_bundle_props()).items():
        if isinstance(namespace_cfg, Mapping):
            _add_search_scopes(
                out,
                base_namespace=namespace,
                raw_scopes=namespace_cfg.get("search_scopes") or namespace_cfg.get("searchScopes"),
            )
        for provider_config in named_service_namespace_provider_configs(_bundle_props(), namespace=namespace):
            _add_search_scopes(
                out,
                base_namespace=str(provider_config.get("namespace") or namespace),
                raw_scopes=provider_config.get("search_scopes") or provider_config.get("searchScopes"),
            )
    return {namespace: scopes for namespace, scopes in out.items() if scopes}


def _action_allowed(namespace: str, action: str) -> bool:
    del namespace, action
    return True


def _endpoint(namespace: str) -> NamedServiceEndpoint | Dict[str, Any]:
    base_ns = _base_namespace(namespace)
    namespace_cfg = _namespace_config(base_ns)
    if not namespace_cfg:
        return _error(
            "named_service_namespace_not_configured",
            f"Namespace {base_ns!r} is not configured under named_services.namespaces.",
            namespace=base_ns,
            requested_namespace=namespace,
        )
    policy = _client_namespace_policy(base_ns)
    if not policy:
        return _error(
            "named_service_client_namespace_not_allowed",
            f"Client {_client_id()!r} is not configured to use namespace {base_ns!r}.",
            namespace=base_ns,
            requested_namespace=namespace,
            client_id=_client_id(),
        )
    provider_configs = named_service_namespace_provider_configs(_bundle_props(), namespace=base_ns)
    if not provider_configs:
        return NamedServiceEndpoint(namespace=base_ns)
    return NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=base_ns)


async def _call(
    *,
    namespace: str,
    tool_name: str,
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
    base_ns = _base_namespace(ns)
    if not ns:
        return _error("named_service_namespace_required", "namespace is required")
    if not _operation_allowed(base_ns, operation):
        return _error(
            "named_service_tool_not_allowed_for_client",
            f"Client {_client_id()!r} is not configured to call tool {tool_name!r} on namespace {base_ns!r}.",
            namespace=base_ns,
            requested_namespace=ns,
            tool=tool_name,
            client_id=_client_id(),
        )
    if action and not _action_allowed(base_ns, action):
        return _error(
            "named_service_action_not_allowed_for_client",
            f"Client {_client_id()!r} is not configured to call action {action!r} on namespace {base_ns!r}.",
            namespace=base_ns,
            requested_namespace=ns,
            action=action,
            client_id=_client_id(),
        )
    endpoint = _endpoint(ns)
    if isinstance(endpoint, dict):
        LOGGER.warning(
            "Named-service client endpoint unavailable:\n"
            "  namespace: %s\n"
            "  operation: %s\n"
            "  client: %s\n"
            "  error: %s",
            ns,
            operation,
            _client_id(),
            endpoint.get("error"),
        )
        return endpoint

    LOGGER.info(
        "Named-service client request start:\n"
        "  namespace: %s\n"
        "  operation: %s\n"
        "  action: %s\n"
        "  client: %s\n"
        "  configured_endpoint:\n"
        "    transport: %s\n"
        "    explicit_bundle: %s\n"
        "    explicit_provider: %s\n"
        "    route: %s\n"
        "    provider_config_count: %s",
        ns,
        operation,
        action or "",
        _client_id(),
        endpoint.transport,
        endpoint.bundle_id or endpoint.module or "",
        endpoint.provider or "",
        endpoint.route,
        len(endpoint.provider_configs or ()),
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
            "base_namespace": base_ns,
        },
        payload=dict(payload or {}),
    )
    LOGGER.info(
        "Named-service client discovery context:\n"
        "  namespace: %s\n"
        "  operation: %s\n"
        "  client: %s\n"
        "  bound: %s",
        ns,
        operation,
        _client_id(),
        get_current_named_service_discovery() is not None,
    )
    response = await call_named_service_endpoint(endpoint, request)
    payload = response.to_dict()
    log_fn = LOGGER.info if payload.get("ok") else LOGGER.warning
    log_fn(
        "Named-service client request complete:\n"
        "  namespace: %s\n"
        "  operation: %s\n"
        "  action: %s\n"
        "  client: %s\n"
        "  ok: %s\n"
        "  error: %s\n"
        "  status: %s",
        ns,
        operation,
        action or "",
        _client_id(),
        payload.get("ok"),
        (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), Mapping) else "",
        response.status,
    )
    return payload


async def provider_about(
    namespace: Annotated[str, "Configured named-service namespace, for example 'sensor'."],
) -> Annotated[Dict[str, Any], "Named service response envelope."]:
    """Describe a configured named-service provider."""

    return await _call(namespace=namespace, tool_name="provider_about", operation=PROVIDER_ABOUT)


async def list_objects(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor' or 'sensor:temperature'."],
    collection: Annotated[str, "Optional namespace-service collection."] = "",
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
        tool_name="list_objects",
        operation=OBJECT_LIST,
        collection=collection,
        cursor=cursor,
        limit=max(1, min(int(limit or 20), 100)),
        filters=parsed_filters,
    )


async def search_objects(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared searchable scoped namespace, for example 'sensor:temperature' or 'sensor:humidity:aggr'."],
    query: Annotated[str, "Search query. Namespace about/schema declares per-scope semantics and filters."],
    limit: Annotated[int, "Maximum objects to return. Keep this bounded."] = 10,
    cursor: Annotated[str, "Optional pagination cursor from a previous response."] = "",
    filters: Annotated[str, "Optional JSON object with provider-specific filters."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with matching items."]:
    """Search objects in a configured named-service namespace."""

    try:
        parsed_filters = _json_object(filters, field_name="filters")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    result = await _call(
        namespace=namespace,
        tool_name="search_objects",
        operation=OBJECT_SEARCH,
        query=query,
        cursor=cursor,
        limit=max(1, min(int(limit or 10), 50)),
        filters=parsed_filters,
    )
    if result.get("ok"):
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx

            widget = NamedServiceSearchResultsWidget(
                emit_delta=comm_ctx.delta,
                artifact_name=f"named_service.search_results.{_base_namespace(namespace)}",
            )
            await widget.send_search_results(
                namespace=_base_namespace(namespace),
                search_scope=_normalize_namespace(namespace),
                query=str(query or "").strip(),
                filters=parsed_filters,
                result=result,
            )
        except Exception:
            LOGGER.warning(
                "Named-service search result subsystem emission failed",
                exc_info=True,
            )
    return result


async def get_object(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor' or 'sensor:temperature'."],
    object_ref: Annotated[str, "Canonical namespace object ref, for example 'sensor:temperature:reading-123'."] = "",
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
        tool_name="get_object",
        operation=OBJECT_GET,
        object_ref=object_ref,
        object_id=object_id,
        include=parsed_include,
    )


async def host_file(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor'."],
    file_ref: Annotated[str, "A fi:/ef: artifact ref or a local file path under the current ReAct output/workdir."],
    object_ref: Annotated[str, "Optional namespace object/container ref, for example 'sensor:temperature:reading-123'."] = "",
    object_id: Annotated[str, "Optional namespace object/container id when object_ref is not known."] = "",
    filename: Annotated[str, "Optional filename override for the hosted file."] = "",
    mime: Annotated[str, "Optional MIME type override."] = "",
    description: Annotated[str, "Optional namespace-service-visible file description."] = "",
    payload: Annotated[str, "Optional JSON object with namespace-service-specific hosting options."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with the hosted namespace object/ref."]:
    """Host one runtime file/ref in a configured named-service namespace."""

    try:
        parsed_payload = _json_object(payload, field_name="payload")
        hosting_payload = _host_file_payload(
            file_ref=file_ref,
            filename=filename,
            mime=mime,
            description=description,
            extra_payload=parsed_payload,
        )
    except Exception as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        tool_name="host_file",
        operation=OBJECT_HOST_FILE,
        object_ref=object_ref,
        object_id=object_id,
        payload=hosting_payload,
    )


async def object_schema(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor' or 'sensor:humidity:aggr'."],
    object_kind: Annotated[str, "Namespace object kind, for example 'sensor.temperature' or 'sensor.humidity.aggr'."] = "",
    object_ref: Annotated[str, "Canonical object ref when asking for the schema of a concrete ref."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with schema and usage guidance."]:
    """Return provider-defined schema and tool payload guidance for objects in a namespace."""

    payload: Dict[str, Any] = {}
    if object_kind:
        payload["object_kind"] = object_kind
    if object_ref:
        payload["object_ref"] = object_ref
    return await _call(
        namespace=namespace,
        tool_name="object_schema",
        operation=OBJECT_SCHEMA,
        object_ref=object_ref,
        payload=payload,
    )


async def object_action(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor' or 'sensor:temperature'."],
    object_ref: Annotated[str, "Canonical namespace object ref, for example 'sensor:temperature:reading-123'."],
    action: Annotated[str, "Bounded namespace action, for example preview, open, download, or describe."] = "preview",
    payload: Annotated[str, "Optional JSON object with namespace-service-specific action payload."] = "",
) -> Annotated[Dict[str, Any], "Named service response envelope with ret.object, ret.extra, or ret.ui_event."]:
    """Run a bounded action against one object in a configured namespace."""

    try:
        parsed_payload = _json_object(payload, field_name="payload")
    except ValueError as exc:
        return _error("named_service_tool_params_invalid", str(exc))
    return await _call(
        namespace=namespace,
        tool_name="object_action",
        operation=OBJECT_ACTION,
        object_ref=object_ref,
        action=action or "preview",
        payload=parsed_payload,
    )


async def upsert_object(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor'."],
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
        tool_name="upsert_object",
        operation=OBJECT_UPSERT,
        object_ref=object_ref,
        object_id=object_id,
        object_payload=parsed_object,
        base_revision=base_revision,
        idempotency_key=idempotency_key,
    )


async def delete_object(
    namespace: Annotated[str, "Configured named-service namespace or provider-declared scoped namespace, for example 'sensor'."],
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
        tool_name="delete_object",
        operation=OBJECT_DELETE,
        object_ref=object_ref,
        base_revision=base_revision,
        payload=parsed_payload,
    )


def list_tools() -> Dict[str, Dict[str, Any]]:
    tools = {
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
        "host_file": {
            "callable": host_file,
            "description": "Host one runtime file/ref in a configured named-service namespace and return the provider-owned file object/ref.",
        },
        "object_schema": {
            "callable": object_schema,
            "description": "Return provider-defined object schema and named-service tool payload guidance.",
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
    namespaces = [
        str(namespace or "").strip().lower().rstrip(":")
        for namespace in named_service_namespaces(_bundle_props())
        if str(namespace or "").strip()
    ]
    if not namespaces:
        return {}
    visible: Dict[str, Dict[str, Any]] = {}
    search_scopes_by_namespace = _snapshot_search_scopes()
    for tool_name, meta in tools.items():
        if tool_name == "object_action":
            continue
        operation = _TOOL_OPERATIONS.get(tool_name)
        if not operation:
            continue
        applicable_namespaces = _operation_applicable_namespaces(namespaces, operation)
        if applicable_namespaces:
            tool_meta = {
                **meta,
                "namespaces_applicable": applicable_namespaces,
            }
            if tool_name == "search_objects":
                visible_search_scopes = {
                    namespace: search_scopes_by_namespace[namespace]
                    for namespace in applicable_namespaces
                    if search_scopes_by_namespace.get(namespace)
                }
                if visible_search_scopes:
                    tool_meta["search_scopes_by_namespace"] = visible_search_scopes
            visible[tool_name] = {
                **tool_meta,
            }
    return visible


tools = sys.modules[__name__]
