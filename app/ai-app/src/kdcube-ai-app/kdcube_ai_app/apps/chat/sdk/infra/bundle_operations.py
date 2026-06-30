# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import (
    BundleBinaryResponse,
    BundleFileResponse,
    BundleStreamResponse,
)

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
)


LOGGER = logging.getLogger("kdcube.sdk.bundle_operations")


BundleOperationCaller = Callable[["BundleOperationCall"], Awaitable[Mapping[str, Any]]]
BundleOperationStreamCaller = Callable[["BundleOperationStreamCall"], Awaitable["BundleOperationStreamResult"]]
BundleNamedServiceCaller = Callable[["BundleNamedServiceCall"], Awaitable["BundleNamedServiceResult"]]

BUNDLE_OPERATION_CALLER_CV: ContextVar[BundleOperationCaller | None] = ContextVar(
    "BUNDLE_OPERATION_CALLER_CV",
    default=None,
)
BUNDLE_OPERATION_STREAM_CALLER_CV: ContextVar[BundleOperationStreamCaller | None] = ContextVar(
    "BUNDLE_OPERATION_STREAM_CALLER_CV",
    default=None,
)
BUNDLE_NAMED_SERVICE_CALLER_CV: ContextVar[BundleNamedServiceCaller | None] = ContextVar(
    "BUNDLE_NAMED_SERVICE_CALLER_CV",
    default=None,
)


@dataclass(frozen=True)
class BundleOperationCall:
    """Request-bound call to a bundle operation.

    This is an SDK-facing local operation bridge. It is bound by platform
    ingress while invoking a bundle operation, so composed bundles can call a
    peer bundle under the same user session without making an HTTP callback.
    """

    bundle_id: str
    operation: str
    data: dict[str, Any] = field(default_factory=dict)
    tenant: str | None = None
    project: str | None = None
    route: str = "operations"
    http_method: str = "POST"


@dataclass(frozen=True)
class BundleOperationStreamCall:
    bundle_id: str
    operation: str
    data: dict[str, Any] = field(default_factory=dict)
    tenant: str | None = None
    project: str | None = None
    route: str = "operations"
    chunk_size: int = 1024 * 1024


@dataclass(frozen=True)
class BundleOperationStreamResult:
    chunks: AsyncIterable[bytes]
    filename: str | None = None
    media_type: str | None = "application/octet-stream"
    headers: dict[str, str] = field(default_factory=dict)
    status_code: int = 200
    response: dict[str, Any] | None = None


@dataclass(frozen=True)
class BundleNamedServiceCall:
    bundle_id: str
    request: Any
    tenant: str | None = None
    project: str | None = None
    registry_method: str = "named_services"


@dataclass(frozen=True)
class BundleNamedServiceResult:
    value: Any


_DISABLED_PROP_VALUES: frozenset[str] = frozenset({"false", "disable", "disabled", "off", "0"})


def _is_truthy_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() not in _DISABLED_PROP_VALUES


def _enabled_section(props: Mapping[str, Any] | None, kind: str) -> Mapping[str, Any] | None:
    section = (props or {}).get("enabled")
    if not isinstance(section, Mapping):
        return None
    sub = section.get(kind)
    return sub if isinstance(sub, Mapping) else None


def _bundle_enabled(props: Mapping[str, Any] | None) -> bool:
    section = (props or {}).get("enabled")
    if not isinstance(section, Mapping):
        return True
    return _is_truthy_enabled(section.get("bundle"))


def _api_enabled(props: Mapping[str, Any] | None, spec: Any) -> bool:
    sub = _enabled_section(props, "api")
    if sub is None:
        return True
    route_key = f"{spec.route}.{spec.alias}.{spec.http_method}"
    if route_key in sub:
        return _is_truthy_enabled(sub.get(route_key))
    return _is_truthy_enabled(sub.get(f"{spec.alias}.{spec.http_method}"))


def _session_user_type(session: Any) -> str:
    value = getattr(session, "user_type", None)
    text = str(getattr(value, "value", value) or "").strip().lower()
    return text


def _raw_roles_visible(required_roles: tuple[str, ...] | list[str] | None, session: Any) -> bool:
    roles = tuple(str(role or "").strip() for role in (required_roles or ()) if str(role or "").strip())
    if not roles:
        return True
    session_roles = {
        role
        for role in (getattr(session, "roles", None) or [])
        if isinstance(role, str) and role.startswith("kdcube:role:")
    }
    return bool(session_roles & set(roles))


def _policy_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _authority_grants(authority: Mapping[str, Any]) -> set[str]:
    grants: set[str] = set()
    grants.update(_policy_list(authority.get("grants")))
    grants.update(_policy_list(authority.get("scopes")))
    credential = authority.get("credential")
    if isinstance(credential, Mapping):
        attrs = credential.get("attrs")
        if isinstance(attrs, Mapping):
            grants.update(_policy_list(attrs.get("grants")))
            grants.update(_policy_list(attrs.get("scopes")))
            grants.update(_policy_list(attrs.get("scope")))
    return grants


def _authority_policy_visible(auth: Mapping[str, Any] | None, session: Any) -> bool:
    if not isinstance(auth, Mapping):
        return True
    required_authority = str(auth.get("authority_id") or auth.get("authority") or "").strip()
    required_grants = set(_policy_list(auth.get("grants") or auth.get("scopes") or auth.get("required_grants")))
    if not required_authority and not required_grants:
        return True
    raw = getattr(session, "identity_authority", None)
    authority = dict(raw) if isinstance(raw, Mapping) else {}
    if required_authority:
        session_authority = str(
            authority.get("authority_id")
            or authority.get("issuer_authority_id")
            or authority.get("authority")
            or ""
        ).strip()
        if session_authority != required_authority:
            return False
    if required_grants and not _authority_grants(authority).issuperset(required_grants):
        return False
    return True


def _endpoint_visible(
    required_user_types: tuple[str, ...] | list[str] | None,
    required_roles: tuple[str, ...] | list[str] | None,
    session: Any,
    auth: Mapping[str, Any] | None = None,
) -> bool:
    del required_user_types
    return (
        _raw_roles_visible(required_roles, session)
        and _authority_policy_visible(auth, session)
    )


def _deep_merge_bundle_props(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base or {}))
    for key, value in dict(override or {}).items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge_bundle_props(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _apply_bundle_props_to_workflow(*, workflow: Any, props: Mapping[str, Any]) -> dict[str, Any]:
    defaults = copy.deepcopy(getattr(workflow, "bundle_props_defaults", None) or {})
    if not defaults:
        defaults = copy.deepcopy(getattr(workflow, "bundle_props", None) or {})
    merger = getattr(workflow, "_deep_merge_props", None)
    merged = merger(defaults, dict(props or {})) if callable(merger) else _deep_merge_bundle_props(defaults, props or {})
    try:
        setattr(workflow, "bundle_props", merged)
    except Exception:
        return merged
    for hook_name in ("_apply_bundle_props_overrides", "_sync_runtime_ctx_bundle_props"):
        hook = getattr(workflow, hook_name, None)
        if callable(hook):
            try:
                hook()
            except Exception:
                LOGGER.debug("Bundle prop hook failed during local operation apply: %s", hook_name, exc_info=True)
    return getattr(workflow, "bundle_props", None) or merged


async def _invoke_bundle_callable(fn: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    result = await asyncio.to_thread(fn, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _stored_session_by_id(
    redis: Any,
    *,
    tenant: str,
    project: str,
    session_id: str,
) -> Any | None:
    if redis is None or not session_id:
        return None
    try:
        from kdcube_ai_app.auth.sessions import SessionManager

        manager = SessionManager(redis_url="", tenant=tenant, project=project)
        manager.redis = redis
        return await manager.get_session_by_id(session_id)
    except Exception:
        LOGGER.debug(
            "Named-service peer operation could not load session from store: tenant=%s project=%s session_id=%s",
            tenant,
            project,
            session_id,
            exc_info=True,
        )
        return None


def _fallback_session_from_comm_context(comm_context: ExternalEventPayload) -> Any:
    from kdcube_ai_app.auth.sessions import RequestContext, UserSession

    user = getattr(comm_context, "user", None)
    routing = getattr(comm_context, "routing", None)
    return UserSession(
        session_id=str(getattr(routing, "session_id", None) or ""),
        user_type=str(getattr(user, "user_type", None) or "anonymous"),
        fingerprint=getattr(user, "fingerprint", None),
        user_id=getattr(user, "user_id", None),
        username=getattr(user, "username", None),
        roles=list(getattr(user, "roles", None) or []),
        permissions=list(getattr(user, "permissions", None) or []),
        email=getattr(user, "email", None),
        timezone=getattr(user, "timezone", None),
        identity_authority=(
            dict(getattr(user, "identity_authority", None))
            if isinstance(getattr(user, "identity_authority", None), Mapping)
            else None
        ),
        request_context=RequestContext(
            client_ip="",
            user_agent="",
            user_timezone=getattr(user, "timezone", None),
            user_utc_offset_min=getattr(user, "utc_offset_min", None),
        ),
    )


async def _session_for_comm_context(redis: Any, comm_context: ExternalEventPayload, *, tenant: str, project: str) -> Any:
    routing = getattr(comm_context, "routing", None)
    session_id = str(getattr(routing, "session_id", None) or "").strip()
    stored = await _stored_session_by_id(redis, tenant=tenant, project=project, session_id=session_id)
    if stored is not None:
        return _apply_request_projection_to_session(stored, comm_context)
    return _fallback_session_from_comm_context(comm_context)


def _apply_request_projection_to_session(session: Any, comm_context: ExternalEventPayload) -> Any:
    """Preserve request-bound authority projections across local peer calls.

    A persisted session may be older than the current request authentication
    result. For example, a Telegram Mini App Data Bus session is stored under
    the Telegram actor, while each request can project a linked platform
    authority after Connection Hub validates Telegram initData. Local
    bundle-to-bundle calls must carry that current request projection, or
    downstream helpers such as ``identity_family_resolve`` silently fall back to
    the actor-only view.
    """

    user = getattr(comm_context, "user", None)
    raw_authority = getattr(user, "identity_authority", None)
    if not isinstance(raw_authority, Mapping) or not raw_authority:
        return session

    try:
        setattr(session, "identity_authority", dict(raw_authority))
    except Exception:
        return session

    for attr in ("user_id", "username", "email", "fingerprint", "timezone"):
        value = getattr(user, attr, None)
        if value not in (None, ""):
            try:
                setattr(session, attr, value)
            except Exception:
                pass
    user_type = str(getattr(user, "user_type", None) or "").strip()
    if user_type:
        try:
            setattr(session, "user_type", user_type)
        except Exception:
            pass
    for attr in ("roles", "permissions"):
        value = getattr(user, attr, None)
        if isinstance(value, (list, tuple, set)):
            try:
                setattr(session, attr, list(value))
            except Exception:
                pass
    return session


def _target_comm_context(
    source: ExternalEventPayload,
    *,
    bundle_id: str,
    tenant: str,
    project: str,
    session: Any,
) -> ExternalEventPayload:
    routing = getattr(source, "routing", None)
    return ExternalEventPayload(
        request=ExternalEventRequest(request_id=str(uuid4())),
        routing=ExternalEventRouting(
            session_id=str(getattr(routing, "session_id", None) or getattr(session, "session_id", None) or ""),
            bundle_id=bundle_id,
            conversation_id=getattr(routing, "conversation_id", None),
            turn_id=getattr(routing, "turn_id", None),
            socket_id=getattr(routing, "socket_id", None),
        ),
        actor=ExternalEventActor(tenant_id=tenant, project_id=project),
        user=ExternalEventUser(
            user_type=_session_user_type(session) or "anonymous",
            user_id=getattr(session, "user_id", None),
            username=getattr(session, "username", None),
            email=getattr(session, "email", None),
            fingerprint=getattr(session, "fingerprint", None),
            roles=list(getattr(session, "roles", None) or []),
            permissions=list(getattr(session, "permissions", None) or []),
            timezone=getattr(session, "timezone", None),
            utc_offset_min=getattr(getattr(session, "request_context", None), "user_utc_offset_min", None),
            identity_authority=dict(getattr(session, "identity_authority", None) or {}),
        ),
    )


async def _invoke_local_bundle_operation_raw(
    call: BundleOperationCall,
    *,
    comm_context: ExternalEventPayload,
    redis: Any,
    pg_pool: Any,
) -> Any:
    """Invoke a peer bundle operation from a non-HTTP runtime.

    This is the processor/job equivalent of the REST integration bridge: it
    preserves the current tenant/project/user/session context, loads the target
    bundle through the runtime registry, applies descriptor-backed props, checks
    operation visibility, and awaits the target API method directly.
    """

    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
        RedisNamedServiceDiscovery,
        bind_named_service_discovery,
    )
    from kdcube_ai_app.infra.plugin.bundle_loader import (
        BundleSpec,
        apply_api_overrides,
        get_workflow_instance_async,
        provider_surface_auth,
        resolve_bundle_api_endpoint,
    )
    from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props, resolve_bundle_spec_from_store
    from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config, resolve_config_request_secrets

    actor = getattr(comm_context, "actor", None)
    tenant = str(call.tenant or getattr(actor, "tenant_id", None) or "").strip()
    project = str(call.project or getattr(actor, "project_id", None) or "").strip()
    if not tenant or not project:
        raise RuntimeError("Tenant/project scope is required for local bundle operation")
    bundle_id = str(call.bundle_id or "").strip()
    operation = str(call.operation or "").strip()
    route = str(call.route or "operations").strip() or "operations"
    if not bundle_id or not operation:
        raise RuntimeError("bundle_id and operation are required for local bundle operation")

    session = await _session_for_comm_context(redis, comm_context, tenant=tenant, project=project)
    LOGGER.info(
        "Local bundle operation call start: tenant=%s project=%s bundle=%s operation=%s route=%s user_type=%s session_id=%s",
        tenant,
        project,
        bundle_id,
        operation,
        route,
        _session_user_type(session),
        getattr(session, "session_id", None),
    )

    spec_resolved = await resolve_bundle_spec_from_store(redis, tenant=tenant, project=project, bundle_id=bundle_id)
    if spec_resolved is None:
        raise RuntimeError(f"Bundle {bundle_id} not found")

    cfg_req = await resolve_config_request_secrets(
        ConfigRequest(agentic_bundle_id=spec_resolved.id, tenant=tenant, project=project),
        bundle_id=spec_resolved.id,
    )
    wf_config = create_workflow_config(cfg_req)
    wf_config.ai_bundle_spec = spec_resolved
    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(getattr(spec_resolved, "singleton", False)),
    )
    target_context = _target_comm_context(
        comm_context,
        bundle_id=spec_resolved.id,
        tenant=tenant,
        project=project,
        session=session,
    )
    workflow, _mod = await get_workflow_instance_async(
        spec,
        wf_config,
        comm_context=target_context,
        redis=redis,
        pg_pool=pg_pool,
    )

    props = await get_bundle_props(redis, tenant=tenant, project=project, bundle_id=spec_resolved.id)
    endpoint_spec, allowed_methods = resolve_bundle_api_endpoint(
        workflow,
        alias=operation,
        http_method=str(call.http_method or "POST").upper(),
        route=route,
        bundle_id=spec_resolved.id,
    )
    if endpoint_spec is None:
        if allowed_methods:
            raise RuntimeError(
                f"Bundle operation {operation} does not support POST. Allowed: {', '.join(allowed_methods)}"
            )
        raise RuntimeError(f"Bundle does not support operation {operation}")
    endpoint_spec = apply_api_overrides(endpoint_spec, props or {})
    endpoint_auth = provider_surface_auth(
        props or {},
        "api",
        alias=endpoint_spec.alias,
        http_method=endpoint_spec.http_method,
        route=endpoint_spec.route,
    )
    if not _endpoint_visible(endpoint_spec.user_types, endpoint_spec.roles, session, endpoint_auth):
        raise RuntimeError(f"Bundle operation {operation} is not visible to this user")
    _apply_bundle_props_to_workflow(workflow=workflow, props=props or {})
    if not _bundle_enabled(props):
        raise RuntimeError(f"Bundle {spec_resolved.id} is disabled")
    if not _api_enabled(props, endpoint_spec):
        raise RuntimeError(f"Bundle operation {operation} is not available")

    fn = getattr(workflow, endpoint_spec.method_name)
    extra = dict(call.data or {})
    extra.setdefault("user_id", getattr(session, "user_id", None) or getattr(session, "fingerprint", None))
    extra.setdefault("fingerprint", getattr(session, "fingerprint", None))
    nested_caller = make_local_bundle_operation_caller(
        redis=redis,
        pg_pool=pg_pool,
        comm_context=target_context,
    )
    nested_stream_caller = make_local_bundle_operation_stream_caller(
        redis=redis,
        pg_pool=pg_pool,
        comm_context=target_context,
    )
    nested_named_service_caller = make_local_bundle_named_service_caller(
        redis=redis,
        pg_pool=pg_pool,
        comm_context=target_context,
    )
    with (
        bind_current_request_context(target_context, comm=None),
        bind_named_service_discovery(RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)),
        bind_bundle_named_service_caller(nested_named_service_caller),
        bind_bundle_operation_caller(nested_caller),
        bind_bundle_operation_stream_caller(nested_stream_caller),
    ):
        result = await _invoke_bundle_callable(fn, **extra)

    LOGGER.info(
        "Local bundle operation call complete: tenant=%s project=%s bundle=%s operation=%s route=%s result_type=%s",
        tenant,
        project,
        spec_resolved.id,
        operation,
        route,
        type(result).__name__,
    )
    return result


async def invoke_local_bundle_operation(
    call: BundleOperationCall,
    *,
    comm_context: ExternalEventPayload,
    redis: Any,
    pg_pool: Any,
) -> Mapping[str, Any]:
    result = await _invoke_local_bundle_operation_raw(
        call,
        comm_context=comm_context,
        redis=redis,
        pg_pool=pg_pool,
    )
    return result if isinstance(result, Mapping) else {"result": result}


async def _iter_file_chunks(path: str, *, chunk_size: int) -> AsyncIterator[bytes]:
    size = max(1, int(chunk_size or 1024 * 1024))
    with Path(path).open("rb") as fh:
        while True:
            chunk = await asyncio.to_thread(fh.read, size)
            if not chunk:
                break
            yield chunk


async def _iter_single_chunk(data: bytes) -> AsyncIterator[bytes]:
    if data:
        yield data


def _coerce_stream_result(result: Any, *, chunk_size: int) -> BundleOperationStreamResult:
    if isinstance(result, BundleStreamResponse):
        return BundleOperationStreamResult(
            chunks=result.chunks,
            filename=result.filename,
            media_type=result.media_type,
            headers=dict(result.headers or {}),
            status_code=result.status_code,
            response=dict(result.response or {}) if isinstance(result.response, Mapping) else None,
        )
    if isinstance(result, BundleFileResponse):
        return BundleOperationStreamResult(
            chunks=_iter_file_chunks(result.path, chunk_size=chunk_size),
            filename=result.filename,
            media_type=result.media_type or "application/octet-stream",
            headers=dict(result.headers or {}),
            status_code=result.status_code,
            response=dict(result.response or {}) if isinstance(result.response, Mapping) else None,
        )
    if isinstance(result, BundleBinaryResponse):
        return BundleOperationStreamResult(
            chunks=_iter_single_chunk(bytes(result.content or b"")),
            filename=result.filename,
            media_type=result.media_type or "application/octet-stream",
            headers=dict(result.headers or {}),
            status_code=result.status_code,
            response=dict(result.response or {}) if isinstance(result.response, Mapping) else None,
        )
    response = getattr(result, "response", None)
    chunks = getattr(result, "chunks", None)
    if response is not None and chunks is not None:
        response_payload = response.to_dict() if callable(getattr(response, "to_dict", None)) else response
        return BundleOperationStreamResult(
            chunks=chunks,
            filename=getattr(result, "filename", None),
            media_type=getattr(result, "media_type", None) or "application/octet-stream",
            headers=dict(getattr(result, "headers", {}) or {}),
            status_code=int(getattr(result, "status_code", 200) or 200),
            response=dict(response_payload or {}) if isinstance(response_payload, Mapping) else None,
        )
    if isinstance(result, (bytes, bytearray, memoryview)):
        return BundleOperationStreamResult(
            chunks=_iter_single_chunk(bytes(result)),
        )
    if isinstance(result, Mapping):
        raise RuntimeError(str(result.get("error") or result.get("message") or "Bundle operation did not return a byte stream"))
    raise RuntimeError(f"Bundle operation returned unsupported stream response type: {type(result).__name__}")


async def invoke_local_bundle_operation_stream(
    call: BundleOperationStreamCall,
    *,
    comm_context: ExternalEventPayload,
    redis: Any,
    pg_pool: Any,
) -> BundleOperationStreamResult:
    result = await _invoke_local_bundle_operation_raw(
        BundleOperationCall(
            bundle_id=call.bundle_id,
            operation=call.operation,
            data=dict(call.data or {}),
            tenant=call.tenant,
            project=call.project,
            route=call.route,
        ),
        comm_context=comm_context,
        redis=redis,
        pg_pool=pg_pool,
    )
    return _coerce_stream_result(result, chunk_size=call.chunk_size)


async def invoke_local_bundle_named_service(
    call: BundleNamedServiceCall,
    *,
    comm_context: ExternalEventPayload,
    redis: Any,
    pg_pool: Any,
) -> BundleNamedServiceResult:
    """Invoke a peer bundle named-service registry directly in this runtime."""

    from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
        RedisNamedServiceDiscovery,
        bind_named_service_discovery,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client import NamedServiceClient
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.registry import NamedServiceRegistry
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
        NamedServiceRequest,
        NamedServiceResponse,
        NamedServiceStreamResult,
    )
    from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec, get_workflow_instance_async
    from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props, resolve_bundle_spec_from_store
    from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config, resolve_config_request_secrets

    actor = getattr(comm_context, "actor", None)
    tenant = str(call.tenant or getattr(actor, "tenant_id", None) or "").strip()
    project = str(call.project or getattr(actor, "project_id", None) or "").strip()
    if not tenant or not project:
        raise RuntimeError("Tenant/project scope is required for local named-service call")
    bundle_id = str(call.bundle_id or "").strip()
    if not bundle_id:
        raise RuntimeError("bundle_id is required for local named-service call")

    session = await _session_for_comm_context(redis, comm_context, tenant=tenant, project=project)
    LOGGER.info(
        "Local bundle named-service call start: tenant=%s project=%s bundle=%s registry_method=%s user_type=%s session_id=%s",
        tenant,
        project,
        bundle_id,
        call.registry_method,
        _session_user_type(session),
        getattr(session, "session_id", None),
    )

    spec_resolved = await resolve_bundle_spec_from_store(redis, tenant=tenant, project=project, bundle_id=bundle_id)
    if spec_resolved is None:
        raise RuntimeError(f"Bundle {bundle_id} not found")
    cfg_req = await resolve_config_request_secrets(
        ConfigRequest(agentic_bundle_id=spec_resolved.id, tenant=tenant, project=project),
        bundle_id=spec_resolved.id,
    )
    wf_config = create_workflow_config(cfg_req)
    wf_config.ai_bundle_spec = spec_resolved
    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(getattr(spec_resolved, "singleton", False)),
    )
    target_context = _target_comm_context(
        comm_context,
        bundle_id=spec_resolved.id,
        tenant=tenant,
        project=project,
        session=session,
    )
    workflow, _mod = await get_workflow_instance_async(
        spec,
        wf_config,
        comm_context=target_context,
        redis=redis,
        pg_pool=pg_pool,
    )
    props = await get_bundle_props(redis, tenant=tenant, project=project, bundle_id=spec_resolved.id)
    _apply_bundle_props_to_workflow(workflow=workflow, props=props or {})
    if not _bundle_enabled(props):
        raise RuntimeError(f"Bundle {spec_resolved.id} is disabled")

    method_name = str(call.registry_method or "named_services").strip() or "named_services"
    registry_factory = getattr(workflow, method_name, None)
    if not callable(registry_factory) and method_name != "_named_services":
        registry_factory = getattr(workflow, "_named_services", None)
    if not callable(registry_factory):
        raise RuntimeError(f"Bundle {spec_resolved.id} does not expose named-service registry method {method_name!r}")
    registry = registry_factory()
    if not isinstance(registry, NamedServiceRegistry):
        raise RuntimeError(f"Bundle {spec_resolved.id} named-service registry method {method_name!r} returned {type(registry).__name__}")

    request = NamedServiceRequest.coerce(call.request)
    auth = AuthContext.from_external_event_payload(target_context, source="named_service.bundle_registry")
    client = NamedServiceClient(registry, auth_context=auth)
    nested_caller = make_local_bundle_operation_caller(redis=redis, pg_pool=pg_pool, comm_context=target_context)
    nested_stream_caller = make_local_bundle_operation_stream_caller(redis=redis, pg_pool=pg_pool, comm_context=target_context)
    nested_named_service_caller = make_local_bundle_named_service_caller(
        redis=redis,
        pg_pool=pg_pool,
        comm_context=target_context,
    )
    with (
        bind_current_request_context(target_context, comm=None),
        bind_named_service_discovery(RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)),
        bind_bundle_operation_caller(nested_caller),
        bind_bundle_operation_stream_caller(nested_stream_caller),
        bind_bundle_named_service_caller(nested_named_service_caller),
    ):
        raw, entry, req = await client.call_raw(request)
    if entry is None or isinstance(raw, (NamedServiceResponse, NamedServiceStreamResult)):
        value = raw
    elif isinstance(raw, (BundleStreamResponse, BundleFileResponse, BundleBinaryResponse)):
        value = raw
    else:
        value = client._coerce_response(raw, entry=entry, request=req)
    LOGGER.info(
        "Local bundle named-service call complete: tenant=%s project=%s bundle=%s operation=%s result_type=%s",
        tenant,
        project,
        spec_resolved.id,
        req.operation,
        type(value).__name__,
    )
    return BundleNamedServiceResult(value=value)


def make_local_bundle_operation_caller(
    *,
    redis: Any,
    pg_pool: Any,
    comm_context: ExternalEventPayload,
) -> BundleOperationCaller:
    async def _caller(call: BundleOperationCall) -> Mapping[str, Any]:
        return await invoke_local_bundle_operation(
            call,
            comm_context=comm_context,
            redis=redis,
            pg_pool=pg_pool,
        )

    return _caller


def make_local_bundle_operation_stream_caller(
    *,
    redis: Any,
    pg_pool: Any,
    comm_context: ExternalEventPayload,
) -> BundleOperationStreamCaller:
    async def _caller(call: BundleOperationStreamCall) -> BundleOperationStreamResult:
        return await invoke_local_bundle_operation_stream(
            call,
            comm_context=comm_context,
            redis=redis,
            pg_pool=pg_pool,
        )

    return _caller


def make_local_bundle_named_service_caller(
    *,
    redis: Any,
    pg_pool: Any,
    comm_context: ExternalEventPayload,
) -> BundleNamedServiceCaller:
    async def _caller(call: BundleNamedServiceCall) -> BundleNamedServiceResult:
        return await invoke_local_bundle_named_service(
            call,
            comm_context=comm_context,
            redis=redis,
            pg_pool=pg_pool,
        )

    return _caller


@contextmanager
def bind_bundle_operation_caller(caller: BundleOperationCaller | None):
    token = BUNDLE_OPERATION_CALLER_CV.set(caller)
    try:
        yield caller
    finally:
        BUNDLE_OPERATION_CALLER_CV.reset(token)


def get_current_bundle_operation_caller() -> BundleOperationCaller | None:
    return BUNDLE_OPERATION_CALLER_CV.get()


@contextmanager
def bind_bundle_operation_stream_caller(caller: BundleOperationStreamCaller | None):
    token = BUNDLE_OPERATION_STREAM_CALLER_CV.set(caller)
    try:
        yield caller
    finally:
        BUNDLE_OPERATION_STREAM_CALLER_CV.reset(token)


def get_current_bundle_operation_stream_caller() -> BundleOperationStreamCaller | None:
    return BUNDLE_OPERATION_STREAM_CALLER_CV.get()


@contextmanager
def bind_bundle_named_service_caller(caller: BundleNamedServiceCaller | None):
    token = BUNDLE_NAMED_SERVICE_CALLER_CV.set(caller)
    try:
        yield caller
    finally:
        BUNDLE_NAMED_SERVICE_CALLER_CV.reset(token)


def get_current_bundle_named_service_caller() -> BundleNamedServiceCaller | None:
    return BUNDLE_NAMED_SERVICE_CALLER_CV.get()


async def call_bundle_operation(
    *,
    bundle_id: str,
    operation: str,
    data: Mapping[str, Any] | None = None,
    tenant: str | None = None,
    project: str | None = None,
    route: str = "operations",
    http_method: str = "POST",
) -> Mapping[str, Any]:
    caller = get_current_bundle_operation_caller()
    if caller is None:
        raise RuntimeError("No request-bound bundle operation caller is available")
    return await caller(
        BundleOperationCall(
            bundle_id=str(bundle_id or "").strip(),
            operation=str(operation or "").strip(),
            data=dict(data or {}),
            tenant=str(tenant or "").strip() or None,
            project=str(project or "").strip() or None,
            route=str(route or "operations").strip() or "operations",
            http_method=str(http_method or "POST").strip().upper() or "POST",
        )
    )


async def call_bundle_operation_stream(
    *,
    bundle_id: str,
    operation: str,
    data: Mapping[str, Any] | None = None,
    tenant: str | None = None,
    project: str | None = None,
    route: str = "operations",
    chunk_size: int = 1024 * 1024,
) -> BundleOperationStreamResult:
    caller = get_current_bundle_operation_stream_caller()
    if caller is None:
        raise RuntimeError("No request-bound bundle operation stream caller is available")
    return await caller(
        BundleOperationStreamCall(
            bundle_id=str(bundle_id or "").strip(),
            operation=str(operation or "").strip(),
            data=dict(data or {}),
            tenant=str(tenant or "").strip() or None,
            project=str(project or "").strip() or None,
            route=str(route or "operations").strip() or "operations",
            chunk_size=max(1, int(chunk_size or 1024 * 1024)),
        )
    )


async def call_bundle_named_service(
    *,
    bundle_id: str,
    request: Any,
    tenant: str | None = None,
    project: str | None = None,
    registry_method: str = "named_services",
) -> BundleNamedServiceResult:
    caller = get_current_bundle_named_service_caller()
    if caller is None:
        raise RuntimeError("No request-bound bundle named-service caller is available")
    return await caller(
        BundleNamedServiceCall(
            bundle_id=str(bundle_id or "").strip(),
            request=request,
            tenant=str(tenant or "").strip() or None,
            project=str(project or "").strip() or None,
            registry_method=str(registry_method or "named_services").strip() or "named_services",
        )
    )


__all__ = [
    "BundleOperationCall",
    "BundleOperationCaller",
    "BundleNamedServiceCall",
    "BundleNamedServiceCaller",
    "BundleNamedServiceResult",
    "BundleOperationStreamCall",
    "BundleOperationStreamCaller",
    "BundleOperationStreamResult",
    "bind_bundle_named_service_caller",
    "bind_bundle_operation_caller",
    "bind_bundle_operation_stream_caller",
    "call_bundle_named_service",
    "call_bundle_operation",
    "call_bundle_operation_stream",
    "get_current_bundle_named_service_caller",
    "get_current_bundle_operation_caller",
    "get_current_bundle_operation_stream_caller",
    "invoke_local_bundle_named_service",
    "invoke_local_bundle_operation",
    "invoke_local_bundle_operation_stream",
    "make_local_bundle_named_service_caller",
    "make_local_bundle_operation_caller",
    "make_local_bundle_operation_stream_caller",
]
