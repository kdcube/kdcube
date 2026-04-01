from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolModuleContext:
    service: Any
    integrations: dict[str, Any]
    tool_subsystem: Any
    communicator: Any
    kv_cache: Any
    ctx_client: Any


def _set(target: Any, name: str, value: Any) -> None:
    if isinstance(target, dict):
        target[name] = value
    else:
        setattr(target, name, value)


def _get(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _derive_communicator(integrations: Mapping[str, Any] | None) -> Any:
    ints = dict(integrations or {})
    tool_subsystem = ints.get("tool_subsystem")
    communicator = getattr(tool_subsystem, "comm", None)
    if communicator is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm

            communicator = get_comm()
        except Exception:
            communicator = None
    return communicator


def apply_bound_context(
    target: Any,
    *,
    svc: Any = None,
    registry: Any = None,
    integrations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ints = dict(integrations or {})
    tool_subsystem = ints.get("tool_subsystem")
    communicator = _derive_communicator(ints)
    kv_cache = ints.get("kv_cache")
    ctx_client = ints.get("ctx_client")

    if svc is not None:
        _set(target, "_SERVICE", svc)
        _set(target, "SERVICE", svc)
        _set(target, "model_service", svc)
    if registry is not None:
        _set(target, "REGISTRY", registry)
    if integrations is not None:
        _set(target, "_INTEGRATIONS", ints)
        _set(target, "INTEGRATIONS", ints)
        _set(target, "_TOOL_SUBSYSTEM", tool_subsystem)
        _set(target, "TOOL_SUBSYSTEM", tool_subsystem)
        _set(target, "_COMMUNICATOR", communicator)
        _set(target, "COMMUNICATOR", communicator)
        _set(target, "_KV_CACHE", kv_cache)
        _set(target, "KV_CACHE", kv_cache)
        _set(target, "_CTX_CLIENT", ctx_client)
        _set(target, "CTX_CLIENT", ctx_client)
    return ints


def bind_module_target(target: Any, *, svc: Any, registry: Any = None, integrations: Mapping[str, Any] | None = None) -> None:
    if _get(target, "__KDCUBE_BIND_DONE__"):
        return

    if hasattr(target, "bind_service") and callable(target.bind_service):
        target.bind_service(svc)
    elif hasattr(target, "set_service") and callable(target.set_service):
        target.set_service(svc)

    if registry is not None and hasattr(target, "bind_registry") and callable(target.bind_registry):
        target.bind_registry(registry)

    if integrations is not None and hasattr(target, "bind_integrations") and callable(target.bind_integrations):
        target.bind_integrations(integrations)

    apply_bound_context(target, svc=svc, registry=registry, integrations=integrations)
    _set(target, "__KDCUBE_BIND_DONE__", True)


def get_bound_context(source: Mapping[str, Any] | Any) -> ToolModuleContext:
    ints = dict(_get(source, "_INTEGRATIONS") or _get(source, "INTEGRATIONS") or {})
    tool_subsystem = _get(source, "_TOOL_SUBSYSTEM") or _get(source, "TOOL_SUBSYSTEM")
    communicator = (
        _get(source, "_COMMUNICATOR")
        or _get(source, "COMMUNICATOR")
        or getattr(tool_subsystem, "comm", None)
    )
    if communicator is None:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_comm

            communicator = get_comm()
        except Exception:
            communicator = None
    return ToolModuleContext(
        service=_get(source, "_SERVICE") or _get(source, "SERVICE"),
        integrations=ints,
        tool_subsystem=tool_subsystem,
        communicator=communicator,
        kv_cache=_get(source, "_KV_CACHE") or _get(source, "KV_CACHE") or ints.get("kv_cache"),
        ctx_client=_get(source, "_CTX_CLIENT") or _get(source, "CTX_CLIENT") or ints.get("ctx_client"),
    )


async def get_shared_browser_service():
    from kdcube_ai_app.infra.rendering.shared_browser import get_shared_browser

    return await get_shared_browser()
