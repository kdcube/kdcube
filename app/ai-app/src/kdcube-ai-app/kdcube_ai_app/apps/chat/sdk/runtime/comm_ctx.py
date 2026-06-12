# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/comm_ctx.py
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Dict, Any, Callable, Mapping

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload

# Public: holds the communicator for the *current execution context*
COMM_CV: ContextVar[object | None] = ContextVar("COMM_CV", default=None)
REQUEST_CONTEXT_CV: ContextVar[ExternalEventPayload | None] = ContextVar("REQUEST_CONTEXT_CV", default=None)
BUNDLE_ID_CV: ContextVar[str | None] = ContextVar("BUNDLE_ID_CV", default=None)
BUNDLE_CALL_CONTEXT_CV: ContextVar[Dict[str, Any]] = ContextVar("BUNDLE_CALL_CONTEXT_CV", default={})
NAMED_SERVICE_DISCOVERY_CV: ContextVar[Dict[str, Any]] = ContextVar("NAMED_SERVICE_DISCOVERY_CV", default={})
TASK_ACTIVITY_TOUCH_CV: ContextVar[Callable[[str], None] | None] = ContextVar("TASK_ACTIVITY_TOUCH_CV", default=None)
_BIND_COMM_UNSET = object()
_BIND_BUNDLE_UNSET = object()
_BIND_BUNDLE_CALL_CONTEXT_UNSET = object()


def _json_safe_mapping(context: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    try:
        return json.loads(json.dumps(dict(context), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("bundle_call_context must be JSON-serializable") from exc


def _sync_request_bundle_call_context(context: Mapping[str, Any] | None) -> None:
    request_context = REQUEST_CONTEXT_CV.get()
    if request_context is not None:
        try:
            request_context.bundle_call_context = dict(context or {})
        except Exception:
            pass

def set_comm(comm: object) -> None:
    """Register the ChatCommunicator singleton for this context."""
    COMM_CV.set(comm)

def get_comm() -> Optional[object|ChatCommunicator]:
    """Get the ChatCommunicator singleton (or None if not initialized)."""
    return COMM_CV.get()


def get_current_comm() -> Optional[object | ChatCommunicator]:
    """Public alias for the current execution communicator."""
    return get_comm()


def set_current_request_context(request_context: ExternalEventPayload | None) -> None:
    """Register the ExternalEventPayload for the current execution context."""
    REQUEST_CONTEXT_CV.set(request_context)


def get_current_request_context() -> Optional[ExternalEventPayload]:
    """Get the current request/task payload bound to this execution context."""
    return REQUEST_CONTEXT_CV.get()


def get_current_user_identity() -> Dict[str, Any]:
    """Return request-bound tenant/project/user identity as a JSON-safe dict."""
    ctx = get_current_request_context()
    if ctx is None:
        return {}
    actor = getattr(ctx, "actor", None)
    user = getattr(ctx, "user", None)
    routing = getattr(ctx, "routing", None)
    roles = getattr(user, "roles", None) or []
    permissions = getattr(user, "permissions", None) or []
    return {
        "tenant_id": getattr(actor, "tenant_id", None),
        "project_id": getattr(actor, "project_id", None),
        "bundle_id": getattr(routing, "bundle_id", None),
        "session_id": getattr(routing, "session_id", None),
        "conversation_id": getattr(routing, "conversation_id", None),
        "turn_id": getattr(routing, "turn_id", None),
        "user_type": getattr(user, "user_type", None),
        "user_id": getattr(user, "user_id", None),
        "username": getattr(user, "username", None),
        "email": getattr(user, "email", None),
        "fingerprint": getattr(user, "fingerprint", None),
        "roles": [str(item) for item in roles],
        "permissions": [str(item) for item in permissions],
        "timezone": getattr(user, "timezone", None),
        "utc_offset_min": getattr(user, "utc_offset_min", None),
    }


def set_current_bundle_call_context(context: Mapping[str, Any] | None) -> None:
    """Register bundle-owned call metadata for tools and child runtimes."""
    safe_context = _json_safe_mapping(context)
    BUNDLE_CALL_CONTEXT_CV.set(safe_context)
    _sync_request_bundle_call_context(safe_context)


def get_current_bundle_call_context() -> Dict[str, Any]:
    """Get JSON-safe bundle-owned call metadata bound to this execution context."""
    value = BUNDLE_CALL_CONTEXT_CV.get({})
    return dict(value) if isinstance(value, dict) else {}


def set_current_named_service_discovery_context(context: Mapping[str, Any] | None) -> None:
    """Register JSON-safe named-service discovery metadata for child runtimes."""
    NAMED_SERVICE_DISCOVERY_CV.set(_json_safe_mapping(context))


def get_current_named_service_discovery_context() -> Dict[str, Any]:
    """Get JSON-safe named-service discovery metadata bound to this execution context."""
    value = NAMED_SERVICE_DISCOVERY_CV.get({})
    return dict(value) if isinstance(value, dict) else {}


def touch_current_task_activity(kind: str) -> bool:
    """Mark processor task activity without emitting a chat event."""
    callback = TASK_ACTIVITY_TOUCH_CV.get()
    if callback is None:
        return False
    callback(str(kind or "sdk.activity"))
    return True


@contextmanager
def bind_current_task_activity_touch(callback: Callable[[str], None] | None):
    token = TASK_ACTIVITY_TOUCH_CV.set(callback)
    try:
        yield callback
    finally:
        TASK_ACTIVITY_TOUCH_CV.reset(token)


@contextmanager
def bind_current_bundle_call_context(context: Mapping[str, Any] | None):
    safe_context = _json_safe_mapping(context)
    request_context = REQUEST_CONTEXT_CV.get()
    previous_request_context_value = None
    if request_context is not None:
        previous_request_context_value = getattr(request_context, "bundle_call_context", None)
    token = BUNDLE_CALL_CONTEXT_CV.set(safe_context)
    _sync_request_bundle_call_context(safe_context)
    try:
        yield BUNDLE_CALL_CONTEXT_CV.get()
    finally:
        BUNDLE_CALL_CONTEXT_CV.reset(token)
        if request_context is not None:
            try:
                request_context.bundle_call_context = (
                    dict(previous_request_context_value or {})
                    if isinstance(previous_request_context_value, Mapping)
                    else {}
                )
            except Exception:
                pass


def update_current_bundle_call_context(patch: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Shallow-merge JSON-safe bundle-owned metadata into the current call context."""
    current = get_current_bundle_call_context()
    current.update(_json_safe_mapping(patch))
    set_current_bundle_call_context(current)
    return get_current_bundle_call_context()


@contextmanager
def bind_current_bundle_call_context_patch(patch: Mapping[str, Any] | None):
    """Temporarily shallow-merge metadata into the current bundle call context."""
    current = get_current_bundle_call_context()
    current.update(_json_safe_mapping(patch))
    with bind_current_bundle_call_context(current) as bound:
        yield bound


def set_current_bundle_id(bundle_id: str | None) -> None:
    """Register current bundle id for this execution context."""
    BUNDLE_ID_CV.set(str(bundle_id or "").strip() or None)


def get_current_bundle_id() -> str | None:
    """Get the current bundle id bound to this execution context."""
    return BUNDLE_ID_CV.get()


@contextmanager
def bind_current_bundle_id(bundle_id: str | None):
    token = BUNDLE_ID_CV.set(str(bundle_id or "").strip() or None)
    try:
        yield BUNDLE_ID_CV.get()
    finally:
        BUNDLE_ID_CV.reset(token)


@contextmanager
def bind_current_request_context(
    request_context: ExternalEventPayload | None,
    *,
    comm: object | ChatCommunicator | None = _BIND_COMM_UNSET,
    bundle_id: str | None | object = _BIND_BUNDLE_UNSET,
    bundle_call_context: Mapping[str, Any] | None | object = _BIND_BUNDLE_CALL_CONTEXT_UNSET,
):
    """
    Bind request-local execution context for the lifetime of one invocation.
    ContextVar binding is task-local and survives across awaits within that task.
    """
    req_token = REQUEST_CONTEXT_CV.set(request_context)
    comm_token = None
    bundle_token = None
    bundle_call_token = None
    if comm is not _BIND_COMM_UNSET:
        comm_token = COMM_CV.set(comm)
    resolved_bundle_id = bundle_id
    if resolved_bundle_id is _BIND_BUNDLE_UNSET and request_context is not None:
        resolved_bundle_id = str(
            getattr(getattr(request_context, "routing", None), "bundle_id", None) or ""
        ).strip() or None
    if resolved_bundle_id is not _BIND_BUNDLE_UNSET:
        bundle_token = BUNDLE_ID_CV.set(str(resolved_bundle_id or "").strip() or None)
    resolved_bundle_call_context = bundle_call_context
    if resolved_bundle_call_context is _BIND_BUNDLE_CALL_CONTEXT_UNSET and request_context is not None:
        resolved_bundle_call_context = getattr(request_context, "bundle_call_context", None)
    if resolved_bundle_call_context is not _BIND_BUNDLE_CALL_CONTEXT_UNSET:
        bundle_call_token = BUNDLE_CALL_CONTEXT_CV.set(
            dict(resolved_bundle_call_context or {}) if isinstance(resolved_bundle_call_context, Mapping) else {}
        )
    try:
        yield request_context
    finally:
        if bundle_call_token is not None:
            BUNDLE_CALL_CONTEXT_CV.reset(bundle_call_token)
        if bundle_token is not None:
            BUNDLE_ID_CV.reset(bundle_token)
        if comm_token is not None:
            COMM_CV.reset(comm_token)
        REQUEST_CONTEXT_CV.reset(req_token)

async def delta(text: str, index: int, marker: str = "answer", completed: bool = False, **kwargs) -> None:
    """Convenience: emit a delta if communicator is present."""
    comm = get_comm()
    if comm is None:
        return
    await comm.delta(text=text, index=index, marker=marker, completed=completed, **kwargs)

async def step(step: str, status: str, **payload) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.step(step=step, status=status, **payload)


async def data_bus_publish(**kwargs) -> Any:
    """Publish a durable Data Bus message through the current communicator."""
    comm = get_comm()
    data_bus = getattr(comm, "data_bus", None) if comm is not None else None
    publish = getattr(data_bus, "publish", None)
    if not callable(publish):
        raise RuntimeError("current communicator does not expose data_bus.publish")
    return await publish(**kwargs)


async def data_bus_publish_and_wait(**kwargs) -> Dict[str, Any]:
    """Publish a durable Data Bus message and wait for the handler result."""
    comm = get_comm()
    data_bus = getattr(comm, "data_bus", None) if comm is not None else None
    publish_and_wait = getattr(data_bus, "publish_and_wait", None)
    if not callable(publish_and_wait):
        raise RuntimeError("current communicator does not expose data_bus.publish_and_wait")
    return await publish_and_wait(**kwargs)

async def complete(data: Dict[str, Any] | None = None) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.complete(data=data or {})

async def error(message: str, data: Dict[str, Any] | None = None) -> None:
    comm = get_comm()
    if comm is None:
        return
    await comm.error(message=message, data=data or {})

# We rebuild the communicator from PORTABLE_SPEC in the child, so we do NOT
# serialize the COMM_CV object itself (not JSON-safe). Keep tiny helpers:

def snapshot_ctxvars() -> dict:
    request_context = REQUEST_CONTEXT_CV.get()
    if request_context is not None and hasattr(request_context, "model_dump"):
        request_context = request_context.model_dump()
    # communicator itself is rebuilt from spec in child runtimes
    return {
        "COMM_PRESENT": COMM_CV.get() is not None,
        "REQUEST_CONTEXT": request_context,
        "BUNDLE_ID": BUNDLE_ID_CV.get(),
        "BUNDLE_CALL_CONTEXT": get_current_bundle_call_context(),
        "NAMED_SERVICE_DISCOVERY": get_current_named_service_discovery_context(),
    }

def restore_ctxvars(payload: dict) -> None:
    raw_request_context = (payload or {}).get("REQUEST_CONTEXT")
    restored_request_context = None
    if raw_request_context:
        try:
            restored_request_context = ExternalEventPayload.model_validate(raw_request_context)
            REQUEST_CONTEXT_CV.set(restored_request_context)
        except Exception:
            REQUEST_CONTEXT_CV.set(None)
    else:
        REQUEST_CONTEXT_CV.set(None)
    BUNDLE_ID_CV.set((payload or {}).get("BUNDLE_ID") or None)
    bundle_call_context = (payload or {}).get("BUNDLE_CALL_CONTEXT")
    if not isinstance(bundle_call_context, dict) and restored_request_context is not None:
        bundle_call_context = getattr(restored_request_context, "bundle_call_context", None)
    BUNDLE_CALL_CONTEXT_CV.set(dict(bundle_call_context or {}) if isinstance(bundle_call_context, dict) else {})
    discovery_context = (payload or {}).get("NAMED_SERVICE_DISCOVERY")
    NAMED_SERVICE_DISCOVERY_CV.set(dict(discovery_context or {}) if isinstance(discovery_context, dict) else {})
