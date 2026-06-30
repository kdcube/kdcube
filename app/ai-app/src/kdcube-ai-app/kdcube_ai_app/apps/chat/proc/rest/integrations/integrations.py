# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/proc/rest/integrations/integrations.py
import asyncio
import contextlib
import copy
import html
import inspect
import json
import logging
import os
import re
import time
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Set, List, Tuple, Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

from kdcube_ai_app.apps.chat.emitters import build_comm_from_comm_context
from kdcube_ai_app.apps.chat.ingress.resolvers import (
    require_auth,
    auth_without_pressure,
    get_user_session_dependency,
)
from kdcube_ai_app.apps.middleware.gateway import STATE_STREAM_ID, extract_stream_id
from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.infra.service_hub.inventory import (
    ConfigRequest,
    create_workflow_config,
    resolve_config_request_secrets,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventActor,
    ExternalEventUser,
    ExternalEventRequest,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery import (
    RedisNamedServiceDiscovery,
    bind_named_service_discovery,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleNamedServiceCall,
    BundleNamedServiceResult,
    BundleOperationCall,
    BundleOperationStreamCall,
    BundleOperationStreamResult,
    bind_bundle_named_service_caller,
    bind_bundle_operation_caller,
    bind_bundle_operation_stream_caller,
    invoke_local_bundle_named_service,
    invoke_local_bundle_operation_stream,
)
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import (
    BundleBinaryResponse,
    BundleFileResponse,
    BundleStreamResponse,
    BundleUploadedFile,
)
from kdcube_ai_app.apps.chat.sdk.infra.control_plane.storage import summarize_registry_bundles
from kdcube_ai_app.infra.plugin.bundle_store import (
    load_registry,
    BundlesRegistry,
    describe_authoritative_bundle_store,
    resolve_bundle_spec_from_store,
    get_bundle_props as store_get_bundle_props,
    _get_bundle_props_from_authority as store_get_bundle_props_from_authority,
    patch_bundle_props as store_patch_bundle_props,
    put_bundle_props as store_put_bundle_props,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    APIEndpointSpec,
    BundleInterfaceManifest,
    MCPEndpointSpec,
    UIWidgetSpec,
    apply_api_overrides,
    apply_bundle_overrides,
    apply_mcp_overrides,
    apply_widget_overrides,
    cache_key_for_spec,
    canonical_enabled_path,
    canonical_provider_surface_path,
    discover_bundle_interface_manifest,
    evict_bundle_scope,
    get_cached_manifest,
    get_workflow_instance_async,
    is_static_bundle_entrypoint_path,
    load_bundle_manifest,
    peek_cached_singleton_for_spec,
    provider_surface_auth,
    resolve_bundle_api_endpoint,
    resolve_bundle_mcp_endpoint,
    run_static_bundle_entrypoint_load_once,
    static_bundle_entrypoint_load_key,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.surface_guard import (
    authorize_delegated_mcp_request,
    mcp_auth_mode,
)
from kdcube_ai_app.infra.secrets import (
    SecretsManagerError,
    SecretsManagerWriteError,
    build_user_secret_metadata_key,
    get_secrets_manager,
)
import kdcube_ai_app.infra.namespaces as namespaces

logger = logging.getLogger("ChatProc.Integrations")

_HTML_HEAD_OPEN_RE = re.compile(r"(<head\b[^>]*>)", re.IGNORECASE)
_HTML_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)

_KDCUBE_RESIZE_REPORTER_SCRIPT = (
    "<script data-kdcube-resize-reporter=\"true\">"
    "(function(){"
    "if(window.__kdcubeResizeReporterInstalled){return;}"
    "window.__kdcubeResizeReporterInstalled=true;"
    "var scheduled=false;"
    "var pendingSince=0;"
    "var stableTimer=null;"
    "var lastHeight=0,lastWidth=0,lastContentWidth=0,lastViewportWidth=0;"
    "var seq=0,lastReason='init';"
    "var stableDelay=80,maxWait=500;"
    "var minTrustedViewportWidth=280;"
    "var debugEnabled=false;"
    "try{debugEnabled=/[?&]kdcube_resize_debug=1(?:&|$)/.test(window.location.search)||(window.localStorage&&window.localStorage.getItem('kdcube.resize.debug')==='1');}catch(e){}"
    "function debug(label,payload){"
    "if(!debugEnabled||!window.console||!console.debug){return;}"
    "console.debug('[kdcube-resize]',label,payload||{});"
    "}"
    "function maxDim(name){"
    "var d=document.documentElement,b=document.body;"
    "return Math.max("
    "d?d['scroll'+name]:0,d?d['offset'+name]:0,d?d['client'+name]:0,"
    "b?b['scroll'+name]:0,b?b['offset'+name]:0,b?b['client'+name]:0"
    ");"
    "}"
    # contentHeight(): a height that can SHRINK. Measure the body box (+ its own
    # margins), NOT documentElement — documentElement.scroll/offset/clientHeight
    # are floored at the iframe viewport (scrollHeight >= clientHeight, and <html>
    # fills the frame), so once a tile grows it can never report smaller and the
    # host iframe stays oversized with an empty gap below the content. The body
    # box is content-sized (height:auto), so it tracks content down as well as up.
    # Apps that fill the frame on purpose (body{min-height:100vh}/height:100%)
    # keep a body box >= the viewport and are unaffected — they never shrink below
    # it. Width still uses maxDim (horizontal-overflow detection is unchanged).
    "function contentHeight(){"
    "var b=document.body,d=document.documentElement;"
    "if(!b){return d?Math.max(d.scrollHeight,d.offsetHeight,d.clientHeight):0;}"
    "var cs=null;try{cs=window.getComputedStyle?window.getComputedStyle(b):null;}catch(e){}"
    "var mt=cs?parseFloat(cs.marginTop)||0:0;"
    "var mb=cs?parseFloat(cs.marginBottom)||0:0;"
    "return Math.ceil(Math.max(b.offsetHeight,b.scrollHeight)+mt+mb);"
    "}"
    "function measure(){"
    "var d=document.documentElement,b=document.body;"
    "var viewportWidth=Math.max(window.innerWidth||0,d?d.clientWidth:0,b?b.clientWidth:0);"
    "var contentWidth=maxDim('Width');"
    "var height=contentHeight();"
    "var width=(contentWidth>viewportWidth+1)?contentWidth:0;"
    "return {height:height,width:width,contentWidth:contentWidth,viewportWidth:viewportWidth,trustedViewport:viewportWidth>=minTrustedViewportWidth,reason:lastReason};"
    "}"
    "function post(size){"
    "if(!window.parent||window.parent===window){debug('skip:no-parent',size);return;}"
    "var height=Math.ceil(Number(size&&size.height)||0);"
    "var width=Math.ceil(Number(size&&size.width)||0);"
    "var contentWidth=Math.ceil(Number(size&&size.contentWidth)||0);"
    "var viewportWidth=Math.ceil(Number(size&&size.viewportWidth)||0);"
    "if(size&&size.trustedViewport===false){debug('skip:untrusted-viewport',size);return;}"
    "if(height<=0){debug('skip:empty-height',size);return;}"
    "if(height===lastHeight&&width===lastWidth&&contentWidth===lastContentWidth&&viewportWidth===lastViewportWidth){debug('skip:duplicate',size);return;}"
    "lastHeight=height;lastWidth=width;lastContentWidth=contentWidth;lastViewportWidth=viewportWidth;"
    "var message={type:'kdcube-resize',height:height,width:width,contentWidth:contentWidth,viewportWidth:viewportWidth,seq:++seq,reason:String(size&&size.reason||lastReason||''),minTrustedViewportWidth:minTrustedViewportWidth};"
    "debug('post',message);"
    "window.parent.postMessage(message,'*');"
    "}"
    "function flush(){"
    "stableTimer=null;pendingSince=0;"
    "post(measure());"
    "}"
    "function report(){"
    "scheduled=false;"
    "if(!pendingSince){pendingSince=Date.now();}"
    "if(stableTimer){clearTimeout(stableTimer);}"
    "var delay=(Date.now()-pendingSince>=maxWait)?0:stableDelay;"
    "stableTimer=setTimeout(flush,delay);"
    "}"
    "function schedule(reason){"
    "if(reason){lastReason=String(reason);}"
    "if(scheduled){return;}"
    "scheduled=true;"
    "(window.requestAnimationFrame||function(fn){return setTimeout(fn,16);})(report);"
    "}"
    "window.addEventListener('load',function(){schedule('load');},{passive:true});"
    "window.addEventListener('resize',function(){schedule('window.resize');},{passive:true});"
    "document.addEventListener('DOMContentLoaded',function(){schedule('domcontentloaded');},{passive:true});"
    "window.addEventListener('message',function(event){"
    "var data=event.data||{};"
    "if(data.type!=='kdcube-resize'){return;}"
    "var frames=document.getElementsByTagName('iframe');"
    "for(var i=0;i<frames.length;i++){"
    "if(frames[i].contentWindow===event.source){"
    "var h=Number(data.height),w=Number(data.width);"
    "if(isFinite(h)&&h>0){frames[i].style.height=Math.ceil(h)+'px';}"
    "if(isFinite(w)&&w>0){frames[i].style.minWidth=Math.ceil(w)+'px';}"
    "break;"
    "}"
    "}"
    "schedule('child-message');"
    "});"
    "if(window.ResizeObserver){"
    "var ro=new ResizeObserver(function(){schedule('resize-observer');});"
    "if(document.documentElement){ro.observe(document.documentElement);}"
    "if(document.body){ro.observe(document.body);}"
    "}"
    "if(window.MutationObserver){"
    "var mo=new MutationObserver(function(){schedule('mutation-observer');});"
    "mo.observe(document.documentElement||document,{childList:true,subtree:true,attributes:true,characterData:true});"
    "}"
    "setTimeout(function(){schedule('timer:0');},0);setTimeout(function(){schedule('timer:250');},250);setTimeout(function(){schedule('timer:1000');},1000);"
    "})();"
    "</script>"
)


def _inject_kdcube_resize_reporter(content: str, *, base_href: str | None = None) -> str:
    injection_parts: List[str] = []
    if base_href:
        injection_parts.append(f"<base href=\"{html.escape(base_href, quote=True)}\">")
    if "data-kdcube-resize-reporter" not in content:
        injection_parts.append(_KDCUBE_RESIZE_REPORTER_SCRIPT)
    if not injection_parts:
        return content

    injection = "".join(injection_parts)
    if _HTML_HEAD_OPEN_RE.search(content):
        return _HTML_HEAD_OPEN_RE.sub(lambda match: f"{match.group(1)}{injection}", content, count=1)
    if _HTML_BODY_CLOSE_RE.search(content):
        return _HTML_BODY_CLOSE_RE.sub(lambda match: f"{injection}{match.group(0)}", content, count=1)
    return f"{injection}{content}"


_integrations_limit: Optional[int] = None
_integrations_semaphore = None

_DISABLED_PROP_VALUES: frozenset = frozenset({"false", "disable", "disabled", "off", "0"})


def _is_truthy_enabled(value: Any) -> bool:
    """Interpret a bundle-props value as a feature switch (default = enabled)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() not in _DISABLED_PROP_VALUES


def _enabled_section(props: Optional[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    """Return the ``enabled.<kind>`` sub-dict from bundle props, or None if absent."""
    section = (props or {}).get("enabled")
    if not isinstance(section, dict):
        return None
    sub = section.get(kind)
    if not isinstance(sub, dict):
        return None
    return sub


def is_bundle_enabled(props: Optional[Dict[str, Any]]) -> bool:
    """Resolve ``enabled.bundle`` against bundle props (default = enabled)."""
    section = (props or {}).get("enabled")
    if not isinstance(section, dict):
        return True
    return _is_truthy_enabled(section.get("bundle"))


def is_api_enabled(props: Optional[Dict[str, Any]], spec: APIEndpointSpec) -> bool:
    """Resolve API enabled overrides.

    Canonical keys are route-aware: ``enabled.api["<route>.<alias>.<METHOD>"]``.
    The legacy ``enabled.api["<alias>.<METHOD>"]`` key remains as a fallback for
    already persisted descriptors.
    """
    sub = _enabled_section(props, "api")
    if sub is None:
        return True
    route_key = f"{spec.route}.{spec.alias}.{spec.http_method}"
    if route_key in sub:
        return _is_truthy_enabled(sub.get(route_key))
    return _is_truthy_enabled(sub.get(f"{spec.alias}.{spec.http_method}"))


def is_widget_enabled(props: Optional[Dict[str, Any]], spec: UIWidgetSpec) -> bool:
    """Resolve ``enabled.widget.<alias>`` (nested)."""
    sub = _enabled_section(props, "widget")
    if sub is None:
        return True
    return _is_truthy_enabled(sub.get(spec.alias))


def is_mcp_enabled(props: Optional[Dict[str, Any]], spec: MCPEndpointSpec) -> bool:
    """Resolve ``enabled.mcp.<alias>`` (nested)."""
    sub = _enabled_section(props, "mcp")
    if sub is None:
        return True
    return _is_truthy_enabled(sub.get(spec.alias))


def _resolve_integrations_limit() -> Optional[int]:
    # Prefer gateway config limits (component-aware).
    try:
        from kdcube_ai_app.infra.gateway.config import get_gateway_config
        cfg = get_gateway_config()
        limits_cfg = getattr(cfg, "limits", None)
        if limits_cfg and limits_cfg.max_integrations_ops_concurrency is not None:
            value = int(limits_cfg.max_integrations_ops_concurrency)
            return value if value > 0 else None
    except Exception:
        pass
    return None


def _get_integrations_semaphore():
    global _integrations_limit, _integrations_semaphore
    limit = _resolve_integrations_limit()
    if not limit:
        _integrations_limit = None
        _integrations_semaphore = None
        return None
    if _integrations_semaphore is None or _integrations_limit != limit:
        _integrations_limit = limit
        _integrations_semaphore = asyncio.Semaphore(limit)
    return _integrations_semaphore


def _get_app_redis(request: Request):
    redis = getattr(request.app.state, "redis_async", None)
    if redis is None:
        raise RuntimeError("redis_async is not initialized on app.state")
    return redis


def _get_app_pg_pool(request: Request):
    pg_pool = getattr(request.app.state, "pg_pool", None)
    if pg_pool is None:
        raise RuntimeError("pg_pool is not initialized on app.state")
    return pg_pool


def _request_stream_id(request: Request) -> Optional[str]:
    value = getattr(request.state, STATE_STREAM_ID, None)
    if isinstance(value, str):
        value = value.strip()
    if value:
        return value
    return extract_stream_id(request)


def _with_implicit_bundle_kwargs(
        extra: Optional[Dict[str, Any]],
        *,
        user_id: Optional[str],
        fingerprint: Optional[str],
) -> Dict[str, Any]:
    merged = dict(extra or {})
    if "user_id" not in merged:
        merged["user_id"] = user_id
    if "fingerprint" not in merged:
        merged["fingerprint"] = fingerprint
    return merged


def _deep_merge_bundle_props(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_bundle_props(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _apply_rest_bundle_props_to_workflow(
        *,
        workflow: Any,
        props: Dict[str, Any],
) -> Dict[str, Any]:
    """
    REST/MCP/widget calls bypass BaseEntrypoint.run(), so apply persisted
    bundle props explicitly before invoking decorated bundle methods.
    """
    defaults = copy.deepcopy(getattr(workflow, "bundle_props_defaults", None) or {})
    if not defaults:
        defaults = copy.deepcopy(getattr(workflow, "bundle_props", None) or {})

    merger = getattr(workflow, "_deep_merge_props", None)
    if callable(merger):
        merged = merger(defaults, props or {})
    else:
        merged = _deep_merge_bundle_props(defaults, props or {})

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
                logger.debug("Bundle prop hook failed during REST apply: %s", hook_name, exc_info=True)
    return getattr(workflow, "bundle_props", None) or merged


def _authoritative_bundle_props(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
) -> Dict[str, Any]:
    """Read bundle props from descriptor authority, never from Redis.

    Request-time bundle interface, widget, API, and MCP serving must reflect the
    descriptor-backed bundle configuration that resolved the bundle, not a
    possibly stale Redis props cache. When no descriptor authority exists,
    callers fall back to code defaults by merging an empty props dict.
    """
    props = store_get_bundle_props_from_authority(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    return dict(props or {})


def _clean_scope_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _resolve_path_scope(*, tenant: str, project: str) -> tuple[str, str]:
    settings = get_settings()
    tenant_id = _clean_scope_value(tenant)
    project_id = _clean_scope_value(project)
    if not tenant_id or not project_id:
        raise HTTPException(status_code=400, detail="Tenant/project scope is required")

    expected_tenant = _clean_scope_value(getattr(settings, "TENANT", None))
    expected_project = _clean_scope_value(getattr(settings, "PROJECT", None))
    if expected_tenant and tenant_id != expected_tenant:
        raise HTTPException(status_code=403, detail="Requested tenant is not served by this proc")
    if expected_project and project_id != expected_project:
        raise HTTPException(status_code=403, detail="Requested project is not served by this proc")
    return tenant_id, project_id


def _bind_route_scope_to_config_request(
        cfg_req: ConfigRequest,
        *,
        tenant: str,
        project: str,
) -> None:
    requested_tenant = _clean_scope_value(getattr(cfg_req, "tenant", None))
    requested_project = _clean_scope_value(getattr(cfg_req, "project", None))
    if requested_tenant and requested_tenant != tenant:
        raise HTTPException(status_code=400, detail="config_request.tenant must match the route tenant")
    if requested_project and requested_project != project:
        raise HTTPException(status_code=400, detail="config_request.project must match the route project")
    cfg_req.tenant = tenant
    cfg_req.project = project


def _build_rest_bundle_routing(*, request: Request, session_id: str, bundle_id: str) -> ExternalEventRouting:
    return ExternalEventRouting(
        session_id=session_id,
        bundle_id=bundle_id,
        socket_id=_request_stream_id(request),
    )


def _unpack_loaded_bundle_workflow(result: tuple[Any, ...]) -> tuple[Any, Any, str, str, Optional[ExternalEventPayload]]:
    if len(result) == 5:
        workflow, spec_resolved, tenant_id, project_id, comm_context = result
        return workflow, spec_resolved, tenant_id, project_id, comm_context
    if len(result) == 4:
        workflow, spec_resolved, tenant_id, project_id = result
        return workflow, spec_resolved, tenant_id, project_id, None
    raise ValueError(f"Unexpected _load_bundle_workflow result shape: {len(result)}")


def _resolve_bound_runtime_comm(*, workflow: Any, comm_context: Optional[ExternalEventPayload]):
    if comm_context is None:
        return None
    descriptor = getattr(type(workflow), "comm", None)
    if isinstance(descriptor, property):
        try:
            candidate = getattr(workflow, "comm")
            if candidate is not None:
                return candidate
        except Exception:
            pass
    try:
        return build_comm_from_comm_context(
            comm_context,
            event_filter=getattr(workflow, "_event_filter", None),
        )
    except Exception:
        return None


def _bind_proc_runtime_services_to_workflow(*, workflow: Any, request: Request) -> None:
    state = getattr(getattr(request, "app", None), "state", None)
    if state is None:
        state = getattr(router, "state", None)
    chat_submitter = getattr(state, "chat_submitter", None) if state is not None else None
    if chat_submitter is not None:
        try:
            setattr(workflow, "chat_submitter", chat_submitter)
        except Exception:
            logger.debug("Failed to bind chat_submitter to workflow", exc_info=True)


def _session_user_type(session: UserSession) -> str | None:
    value = str(getattr(getattr(session, "user_type", None), "value", "") or "").strip()
    return value or None


_USER_TYPE_VISIBILITY_ORDER: dict[str, int] = {
    "anonymous": 0,
    "registered": 1,
    "paid": 2,
    "privileged": 3,
}


def _user_types_visible(required_user_types: tuple[str, ...] | list[str] | None, session: UserSession) -> bool:
    user_types = tuple(
        str(user_type or "").strip().lower()
        for user_type in (required_user_types or ())
        if str(user_type or "").strip()
    )
    if not user_types:
        return True
    current = str(_session_user_type(session) or "").strip().lower()
    if not current:
        return False

    current_rank = _USER_TYPE_VISIBILITY_ORDER.get(current)
    if current_rank is None:
        return current in set(user_types)

    thresholds = [
        _USER_TYPE_VISIBILITY_ORDER[user_type]
        for user_type in user_types
        if user_type in _USER_TYPE_VISIBILITY_ORDER
    ]
    if not thresholds:
        return current in set(user_types)
    return current_rank >= min(thresholds)


def _visible_widget_specs(
        manifest: BundleInterfaceManifest,
        session: UserSession,
        props: Optional[Dict[str, Any]] = None,
) -> list[UIWidgetSpec]:
    """Return widget specs visible to ``session``, with bundle-props overrides applied."""
    out: list[UIWidgetSpec] = []
    for spec in manifest.ui_widgets:
        effective = apply_widget_overrides(spec, props or {})
        auth = provider_surface_auth(props, "widget", alias=effective.alias)
        if is_widget_enabled(props, effective) and _endpoint_visible(effective.user_types, effective.roles, session, auth):
            out.append(effective)
    return out


def _resolve_widget_spec(
        manifest: BundleInterfaceManifest,
        *,
        alias: str,
) -> UIWidgetSpec | None:
    for spec in manifest.ui_widgets:
        if spec.alias == alias:
            return spec
    return None


async def _reload_widget_manifest_after_miss(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        request: Request,
        session: UserSession,
        spec_resolved: Any,
        manifest: BundleInterfaceManifest,
):
    """Recover once from stale in-process bundle code/manifest state."""
    try:
        spec = BundleSpec(
            path=spec_resolved.path,
            module=spec_resolved.module,
            singleton=bool(spec_resolved.singleton),
        )
        eviction = evict_bundle_scope(spec, drop_sys_modules=True)
        logger.warning(
            "Bundle widget lookup miss; evicted local bundle cache and retrying: "
            "tenant=%s project=%s bundle=%s widget=%s path=%s module=%s singleton=%s "
            "manifest_widgets_before=%s eviction=%s pid=%s",
            tenant,
            project,
            bundle_id,
            widget_alias,
            spec_resolved.path,
            spec_resolved.module,
            bool(spec_resolved.singleton),
            [spec.alias for spec in manifest.ui_widgets],
            eviction,
            os.getpid(),
        )
        payload = BundleSuggestionsRequest()
        workflow, retry_spec, tenant_id, project_id, comm_context = _unpack_loaded_bundle_workflow(
            await _load_bundle_workflow(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
                payload=payload,
                request=request,
                session=session,
            )
        )
        retry_manifest = discover_bundle_interface_manifest(workflow, bundle_id=retry_spec.id)
        props = _authoritative_bundle_props(
            tenant=tenant_id,
            project=project_id,
            bundle_id=retry_spec.id,
        )
        workflow_props = _apply_rest_bundle_props_to_workflow(workflow=workflow, props=props)
        widget_spec = _resolve_widget_spec(retry_manifest, alias=widget_alias)
        return workflow, retry_spec, tenant_id, project_id, comm_context, retry_manifest, props, workflow_props, widget_spec
    except Exception:
        logger.warning(
            "Bundle widget lookup retry failed: tenant=%s project=%s bundle=%s widget=%s pid=%s",
            tenant,
            project,
            bundle_id,
            widget_alias,
            os.getpid(),
            exc_info=True,
        )
        return None


def _visible_api_specs(
        manifest: BundleInterfaceManifest,
        session: UserSession,
        props: Optional[Dict[str, Any]] = None,
) -> list[APIEndpointSpec]:
    """Return API specs visible to ``session``, with bundle-props overrides applied."""
    out: list[APIEndpointSpec] = []
    for spec in manifest.api_endpoints:
        effective = apply_api_overrides(spec, props or {})
        auth = provider_surface_auth(
            props,
            "api",
            alias=effective.alias,
            http_method=effective.http_method,
            route=effective.route,
        )
        if is_api_enabled(props, effective) and _endpoint_visible(effective.user_types, effective.roles, session, auth):
            out.append(effective)
    return out


def _visible_mcp_specs(
        manifest: BundleInterfaceManifest,
        session: UserSession,
        props: Optional[Dict[str, Any]] = None,
) -> list[MCPEndpointSpec]:
    """Return MCP specs with bundle-props overrides applied (no visibility filtering)."""
    del session
    out: list[MCPEndpointSpec] = []
    for spec in manifest.mcp_endpoints:
        effective = apply_mcp_overrides(spec, props or {})
        if is_mcp_enabled(props, effective):
            out.append(effective)
    return out


def _user_raw_roles(session: UserSession) -> set[str]:
    """Raw (externally defined) roles: kdcube:role:* entries from session.roles."""
    return {
        r for r in (session.roles or [])
        if isinstance(r, str) and r.startswith("kdcube:role:")
    }


def _raw_roles_visible(required_roles: tuple[str, ...] | list[str] | None, session: UserSession) -> bool:
    roles = tuple(str(role or "").strip() for role in (required_roles or ()) if str(role or "").strip())
    if not roles:
        return True
    return bool(_user_raw_roles(session) & set(roles))


def _policy_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _session_authority(session: UserSession) -> dict[str, Any]:
    authority = getattr(session, "identity_authority", None)
    return dict(authority) if isinstance(authority, Mapping) else {}


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


def _authority_policy_visible(auth: Mapping[str, Any] | None, session: UserSession) -> bool:
    if not isinstance(auth, Mapping):
        return True
    required_authority = str(auth.get("authority_id") or auth.get("authority") or "").strip()
    required_grants = set(_policy_list(auth.get("grants") or auth.get("scopes") or auth.get("required_grants")))
    if not required_authority and not required_grants:
        return True
    authority = _session_authority(session)
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
        session: UserSession,
        auth: Mapping[str, Any] | None = None,
) -> bool:
    return (
        _user_types_visible(required_user_types, session)
        and _raw_roles_visible(required_roles, session)
        and _authority_policy_visible(auth, session)
    )


def _bundle_allowed_for_session(
        manifest: "BundleInterfaceManifest | None",
        session: UserSession,
        props: Optional[Dict[str, Any]] = None,
) -> bool:
    """Bundle-level access check based on allowed_roles declared on @bundle_entrypoint.
    No allowed_roles (empty) means the bundle is visible to all authenticated users.
    When props are provided, provider-surface descriptor policy is applied first."""
    if manifest is None:
        return True
    effective = apply_bundle_overrides(manifest, props or {})
    if not effective.allowed_roles:
        return True
    return bool(_user_raw_roles(session) & set(effective.allowed_roles))


router = APIRouter()
admin_router = APIRouter()
internal_router = APIRouter()

_LOCALHOST = {"127.0.0.1", "::1"}


class BundleSuggestionsRequest(BaseModel):
    conversation_id: Optional[str] = None
    bundle_id: Optional[str] = None
    config_request: Optional[ConfigRequest] = None
    data: Optional[Dict[str, Any]] = None


_BUNDLE_REQUEST_RESERVED_KEYS = {"conversation_id", "bundle_id", "config_request", "data"}


def _normalize_bundle_request_body(raw_body: Any) -> Dict[str, Any]:
    if not isinstance(raw_body, dict):
        return {}

    payload_data = dict(raw_body)
    extra = {
        key: value
        for key, value in payload_data.items()
        if key not in _BUNDLE_REQUEST_RESERVED_KEYS
    }
    if not extra:
        return payload_data

    normalized = {
        key: value
        for key, value in payload_data.items()
        if key in _BUNDLE_REQUEST_RESERVED_KEYS
    }
    existing_data = normalized.get("data")
    if existing_data is None:
        normalized["data"] = extra
        return normalized
    if isinstance(existing_data, dict):
        normalized["data"] = {**extra, **existing_data}
        return normalized
    return normalized


def _attachment_headers(*, filename: Optional[str], headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    out = dict(headers or {})
    if filename and "content-disposition" not in {str(key).lower(): value for key, value in out.items()}:
        out["Content-Disposition"] = f'attachment; filename="{filename}"'
    return out


def _coerce_bundle_http_response(result: Any):
    if isinstance(result, Response):
        return result
    if isinstance(result, BundleBinaryResponse):
        return Response(
            content=result.content,
            media_type=result.media_type,
            headers=_attachment_headers(filename=result.filename, headers=result.headers),
            status_code=result.status_code,
        )
    if isinstance(result, BundleFileResponse):
        return FileResponse(
            result.path,
            media_type=result.media_type,
            filename=result.filename,
            headers=_attachment_headers(filename=result.filename, headers=result.headers),
            status_code=result.status_code,
        )
    if isinstance(result, BundleStreamResponse):
        return StreamingResponse(
            result.chunks,
            media_type=result.media_type,
            headers=_attachment_headers(filename=result.filename, headers=result.headers),
            status_code=result.status_code,
        )
    return None


def _coerce_bundle_mcp_asgi_app(result: Any, *, transport: str):
    if transport == "streamable-http" and hasattr(result, "streamable_http_app"):
        return result.streamable_http_app()
    if callable(result):
        return result
    raise RuntimeError(
        f"Bundle MCP endpoint must return a FastMCP app or ASGI app for transport={transport}"
    )


def _build_mcp_dispatch_path(*, transport: str, mcp_path: str) -> str:
    suffix = str(mcp_path or "").strip("/")
    if transport == "streamable-http":
        return "/mcp" if not suffix else f"/mcp/{suffix}"
    raise RuntimeError(f"Unsupported MCP transport: {transport}")


def _filtered_proxy_headers(headers: httpx.Headers) -> Dict[str, str]:
    blocked = {"content-length", "transfer-encoding", "connection"}
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


async def _dispatch_bundle_mcp_request(
        *,
        request: Request,
        mcp_app: Any,
        transport: str,
        mcp_path: str,
        body: bytes | None = None,
) -> Response:
    body = await request.body() if body is None else body
    dispatch_path = _build_mcp_dispatch_path(transport=transport, mcp_path=mcp_path)
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
    params = list(request.query_params.multi_items())

    async with _bundle_mcp_lifespan_context(mcp_app):
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app),
                base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.request(
                request.method,
                dispatch_path,
                params=params,
                headers=headers,
                content=body,
            )

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=_filtered_proxy_headers(response.headers),
    )


@contextlib.asynccontextmanager
async def _bundle_mcp_lifespan_context(mcp_app: Any):
    lifespan_context = getattr(getattr(mcp_app, "router", None), "lifespan_context", None)
    if not callable(lifespan_context):
        yield
        return
    async with lifespan_context(mcp_app):
        yield


async def _parse_bundle_request_payload(request: Request) -> Tuple[BundleSuggestionsRequest, List[BundleUploadedFile]]:
    method = str(getattr(request, "method", "POST") or "POST").upper()
    if method == "GET":
        return BundleSuggestionsRequest(), []

    content_type = str(request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        try:
            raw_body = await request.json()
        except Exception:
            raw_body = {}
        if not raw_body:
            return BundleSuggestionsRequest(), []
        return BundleSuggestionsRequest.model_validate(_normalize_bundle_request_body(raw_body)), []

    form = await request.form()
    payload_data: Dict[str, Any] = {}
    payload_raw = form.get("payload")
    if isinstance(payload_raw, str) and payload_raw.strip():
        try:
            payload_data = json.loads(payload_raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid multipart payload JSON: {exc}") from exc
    else:
        data_raw = form.get("data")
        if isinstance(data_raw, str) and data_raw.strip():
            try:
                payload_data["data"] = json.loads(data_raw)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid multipart data JSON: {exc}") from exc

        conversation_id = form.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            payload_data["conversation_id"] = conversation_id.strip()
        bundle_id = form.get("bundle_id")
        if isinstance(bundle_id, str) and bundle_id.strip():
            payload_data["bundle_id"] = bundle_id.strip()
        config_raw = form.get("config_request")
        if isinstance(config_raw, str) and config_raw.strip():
            try:
                payload_data["config_request"] = json.loads(config_raw)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid multipart config_request JSON: {exc}") from exc

    uploaded_files: List[BundleUploadedFile] = []
    for field_name, value in form.multi_items():
        if not isinstance(value, (UploadFile, StarletteUploadFile)):
            continue
        raw = await value.read()
        uploaded_files.append(
            BundleUploadedFile(
                filename=value.filename or "file",
                content_type=value.content_type or "application/octet-stream",
                content=raw or b"",
                field_name=str(field_name or "file"),
            )
        )

    return BundleSuggestionsRequest.model_validate(payload_data or {}), uploaded_files


class AdminBundlesUpdateRequest(BaseModel):
    op: str = "merge"  # "replace" | "merge"
    bundles: Dict[str, Dict[str, Any]]
    default_bundle_id: Optional[str] = None
    tenant: Optional[str] = None
    project: Optional[str] = None


class BundlePropsUpdateRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None
    op: str = "replace"  # "replace" | "merge"
    props: Dict[str, Any] = {}


class BundlePropsResetRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None


class BundleSecretsUpdateRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None
    mode: str = "set"  # set | clear
    secrets: Dict[str, Any] = {}


class UserBundleSecretsUpdateRequest(BaseModel):
    mode: str = "set"  # set | clear
    secrets: Dict[str, Any] = {}


class BundleCleanupRequest(BaseModel):
    drop_sys_modules: bool = True
    tenant: Optional[str] = None
    project: Optional[str] = None


class BundleReloadAuthorityRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None
    bundle_id: Optional[str] = None


class BundleStatusRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None
    bundle_id: str


def _bundles_channel(fmt: str, *, tenant: str, project: str) -> str:
    return fmt.format(tenant=tenant, project=project)


def _bundle_props_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.PROPS_KEY_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


def _bundle_secrets_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.SECRETS_KEYS_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


def _bundle_secrets_update_channel(*, tenant: str, project: str) -> str:
    return namespaces.CONFIG.BUNDLES.SECRETS_UPDATE_CHANNEL.format(
        tenant=tenant,
        project=project,
    )


async def _publish_bundle_secrets_update(
        redis,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        scope: str,
        mode: str,
        keys: Set[str],
        actor: Optional[str] = None,
        user_id: Optional[str] = None,
) -> None:
    if redis is None:
        return
    payload: Dict[str, Any] = {
        "type": "bundles.secrets.update",
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "scope": scope,
        "mode": mode,
        "keys": sorted(str(key) for key in keys),
        "ts": time.time(),
    }
    if actor:
        payload["updated_by"] = actor
    if user_id:
        payload["user_id"] = user_id
    try:
        await redis.publish(
            _bundle_secrets_update_channel(tenant=tenant, project=project),
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        logger.warning(
            "Failed to publish bundle secrets update: tenant=%s project=%s bundle=%s scope=%s mode=%s",
            tenant,
            project,
            bundle_id,
            scope,
            mode,
            exc_info=True,
        )


def _user_bundle_secrets_key(*, tenant: str, project: str, bundle_id: str, user_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.USER_SECRETS_KEYS_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        user_id=user_id,
    )


def _flatten_secrets(prefix: str, node: Any, out: Dict[str, str]) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key is None:
                continue
            _flatten_secrets(f"{prefix}.{key}", value, out)
        return
    if isinstance(node, list):
        for idx, value in enumerate(node):
            _flatten_secrets(f"{prefix}.{idx}", value, out)
        return
    value = str(node).strip()
    if not value:
        return
    out[prefix] = value


def _flatten_secret_keys(prefix: str, node: Any, out: Set[str]) -> None:
    if node is None:
        if prefix:
            out.add(prefix)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key is None:
                continue
            _flatten_secret_keys(f"{prefix}.{key}", value, out)
        return
    if isinstance(node, list):
        for idx, value in enumerate(node):
            _flatten_secret_keys(f"{prefix}.{idx}", value, out)
        return
    out.add(prefix)


def _deep_merge_props(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base or {})
    for key, value in (patch or {}).items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_props(base_value, value)
        else:
            merged[key] = value
    return merged


async def _resolve_bundle_spec_from_runtime(
        *,
        request: Request,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
):
    return await resolve_bundle_spec_from_store(
        _get_app_redis(request),
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


async def _load_bundle_props_defaults(
        *,
        bundle_id: str,
        tenant: str,
        project: str,
        request: Request,
        session: UserSession,
        evict_before_load: bool = True,
) -> Dict[str, Any]:
    spec_resolved = await _resolve_bundle_spec_from_runtime(
        request=request,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {bundle_id} not found")

    try:
        wf_config = create_workflow_config(ConfigRequest())
    except Exception:
        wf_config = create_workflow_config(ConfigRequest.model_validate({"project": project}))

    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    if evict_before_load:
        # Reload bundle code for explicit "code defaults" reads/resets. Static
        # UI warmups pass evict_before_load=False so concurrent iframe/widget
        # requests do not cancel an in-flight on_bundle_load build.
        try:
            evict_bundle_scope(spec)
        except Exception:
            pass
    routing = _build_rest_bundle_routing(
        request=request,
        session_id=session.session_id,
        bundle_id=spec_resolved.id,
    )
    comm_context = ExternalEventPayload(
        request=ExternalEventRequest(request_id=str(uuid.uuid4())),
        routing=routing,
        actor=ExternalEventActor(
            tenant_id=tenant,
            project_id=project,
        ),
        user=ExternalEventUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            email=session.email,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.request_context.user_timezone,
            utc_offset_min=session.request_context.user_utc_offset_min,
            identity_authority=dict(getattr(session, "identity_authority", None) or {}),
        ),
    )

    wf_config.ai_bundle_spec = spec_resolved
    redis = _get_app_redis(request)
    pg_pool = _get_app_pg_pool(request)
    workflow, _mod = await get_workflow_instance_async(
        spec, wf_config, comm_context=comm_context, redis=redis, pg_pool=pg_pool,
    )
    defaults = getattr(workflow, "bundle_props_defaults", None) or {}
    defaults = dict(defaults)
    try:
        cfg = getattr(workflow, "configuration", None) or {}
        version = cfg.get("bundle_version")
        if not version:
            version = getattr(getattr(workflow, "config", None), "ai_bundle_spec", None)
            version = getattr(version, "version", None)
        if version:
            defaults["bundle_version"] = str(version)
    except Exception:
        pass
    try:
        if getattr(spec_resolved, "git_commit", None):
            defaults["git_commit"] = str(spec_resolved.git_commit)
        if getattr(spec_resolved, "ref", None):
            defaults["ref"] = str(spec_resolved.ref)
        if getattr(spec_resolved, "repo", None):
            defaults["repo"] = str(spec_resolved.repo)
    except Exception:
        pass
    return defaults


def _bundle_defaults_error_payload(bundle_id: str, exc: Exception) -> Dict[str, Any]:
    message = str(exc).strip() or exc.__class__.__name__
    return {
        "code": exc.__class__.__name__,
        "message": message,
        "where": "_load_bundle_props_defaults",
        "bundle_id": bundle_id,
        "managed": True,
    }


async def _get_bundle_manifest(
        *,
        bundle_id: str,
        tenant: str,
        project: str,
        request: Request,
        session: UserSession,
) -> Optional[BundleInterfaceManifest]:
    """
    Return BundleInterfaceManifest for bundle_id.
    Reads from _manifest_cache when available (populated by get_workflow_instance).
    Falls back to loading the bundle on-demand when the cache is cold.
    """
    spec_resolved = await _resolve_bundle_spec_from_runtime(
        request=request,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    if not spec_resolved:
        return None
    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    cached = get_cached_manifest(spec)
    if cached is not None:
        return cached
    # Fallback: load module and discover class without instantiation.
    try:
        return load_bundle_manifest(spec, bundle_id=spec_resolved.id)
    except Exception:
        logger.warning("[bundle_manifest] Failed to load manifest for %s", bundle_id, exc_info=True)
        return None


def _api_spec_descriptor(spec: APIEndpointSpec, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    effective = apply_api_overrides(spec, props or {})
    auth = provider_surface_auth(
        props,
        "api",
        alias=spec.alias,
        http_method=spec.http_method,
        route=spec.route,
    ) or {}
    policy_path = canonical_provider_surface_path(
        "api",
        alias=spec.alias,
        http_method=spec.http_method,
        route=spec.route,
    )
    return {
        "alias": spec.alias,
        "http_method": spec.http_method,
        "route": spec.route,
        "user_types": list(effective.user_types),
        "user_types_default": list(spec.user_types),
        "user_types_path": f"{policy_path}.visibility.user_types",
        "user_types_overridden": tuple(effective.user_types) != tuple(spec.user_types),
        "roles": list(effective.roles),
        "roles_default": list(spec.roles),
        "roles_path": f"{policy_path}.visibility.roles",
        "roles_overridden": tuple(effective.roles) != tuple(spec.roles),
        "auth": auth,
        "auth_path": f"{policy_path}.auth",
        "authority_id": str(auth.get("authority_id") or auth.get("authority") or ""),
        "grants": list(_policy_list(auth.get("grants") or auth.get("scopes") or auth.get("required_grants"))),
        "enabled_path": canonical_enabled_path(
            "api",
            alias=spec.alias,
            http_method=spec.http_method,
            route=spec.route,
        ),
    }


def _widget_spec_descriptor(spec: UIWidgetSpec, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    effective = apply_widget_overrides(spec, props or {})
    auth = provider_surface_auth(props, "widget", alias=spec.alias) or {}
    policy_path = canonical_provider_surface_path("widget", alias=spec.alias)
    return {
        "alias": spec.alias,
        "icon": spec.icon,
        "user_types": list(effective.user_types),
        "user_types_default": list(spec.user_types),
        "user_types_path": f"{policy_path}.visibility.user_types",
        "user_types_overridden": tuple(effective.user_types) != tuple(spec.user_types),
        "roles": list(effective.roles),
        "roles_default": list(spec.roles),
        "roles_path": f"{policy_path}.visibility.roles",
        "roles_overridden": tuple(effective.roles) != tuple(spec.roles),
        "auth": auth,
        "auth_path": f"{policy_path}.auth",
        "authority_id": str(auth.get("authority_id") or auth.get("authority") or ""),
        "grants": list(_policy_list(auth.get("grants") or auth.get("scopes") or auth.get("required_grants"))),
        "enabled_path": canonical_enabled_path("widget", alias=spec.alias),
    }


def _mcp_spec_descriptor(spec: MCPEndpointSpec, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    effective = apply_mcp_overrides(spec, props or {})
    policy_path = canonical_provider_surface_path("mcp", alias=spec.alias)
    auth = effective.auth if isinstance(effective.auth, Mapping) else {}
    return {
        "alias": spec.alias,
        "route": spec.route,
        "transport": effective.transport,
        "transport_default": spec.transport,
        "transport_config": spec.transport_config,
        "transport_overridden": effective.transport != spec.transport,
        "auth": effective.auth,
        "auth_default": spec.auth,
        "auth_path": f"{policy_path}.auth",
        "authority_id": str(auth.get("authority_id") or auth.get("authority") or ""),
        "grants": list(_policy_list(auth.get("grants") or auth.get("scopes") or auth.get("required_grants"))),
        "auth_mode": mcp_auth_mode(effective.auth),
        "auth_overridden": effective.auth != spec.auth,
        "enabled_path": canonical_enabled_path("mcp", alias=spec.alias),
    }


def _cron_spec_descriptor(spec, props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Serialise a CronJobSpec, computing effective cron_expression / timezone
    after expr_config / tz_config overrides applied against bundle props."""
    from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import (
        resolve_effective_cron,
        resolve_effective_timezone,
    )
    effective_cron = resolve_effective_cron(
        cron_expression=spec.cron_expression,
        expr_config=spec.expr_config,
        props=props or {},
    )
    effective_tz = resolve_effective_timezone(
        timezone_name=spec.timezone,
        tz_config=spec.tz_config,
        props=props or {},
    )
    default_tz = (str(spec.timezone).strip() if spec.timezone else "UTC") or "UTC"
    return {
        "method_name": spec.method_name,
        "alias": spec.alias,
        "cron_expression": effective_cron,
        "cron_expression_default": spec.cron_expression,
        "expr_config": spec.expr_config,
        "cron_expression_overridden": (
            spec.expr_config is not None and effective_cron != spec.cron_expression
        ),
        "timezone": effective_tz,
        "timezone_default": default_tz,
        "tz_config": spec.tz_config,
        "timezone_overridden": (
            spec.tz_config is not None and effective_tz != default_tz
        ),
        "span": spec.span,
        "enabled_path": canonical_enabled_path("cron", alias=spec.alias),
    }


def _authority_provider_spec_descriptor(spec) -> Dict[str, Any]:
    return {
        "method_name": spec.method_name,
        "authority_id": spec.authority_id,
        "authenticator_id": spec.authenticator_id,
        "credential_kinds": list(spec.credential_kinds),
        "audiences": list(spec.audiences),
        "label": spec.label,
        "transports": list(spec.transports),
    }


def _manifest_to_descriptor(
        manifest: BundleInterfaceManifest,
        props: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serialise a full (unfiltered) manifest to a plain dict.

    When ``props`` is provided, effective values reflect descriptor-owned
    provider-surface policy; otherwise effective == decorator defaults.
    """
    effective = apply_bundle_overrides(manifest, props or {})
    return {
        "enabled_path": canonical_enabled_path("bundle"),
        "allowed_roles": list(effective.allowed_roles),
        "allowed_roles_default": list(manifest.allowed_roles),
        "allowed_roles_path": f"{canonical_provider_surface_path('bundle')}.visibility.allowed_roles",
        "allowed_roles_overridden": tuple(effective.allowed_roles) != tuple(manifest.allowed_roles),
        "apis": [_api_spec_descriptor(s, props) for s in manifest.api_endpoints],
        "mcp_endpoints": [_mcp_spec_descriptor(s, props) for s in manifest.mcp_endpoints],
        "widgets": [_widget_spec_descriptor(s, props) for s in manifest.ui_widgets],
        "on_message": manifest.on_message.method_name if manifest.on_message else None,
        "on_job": manifest.on_job.method_name if manifest.on_job else None,
        "scheduled_jobs": [_cron_spec_descriptor(s, props=props) for s in manifest.scheduled_jobs],
        "data_bus_handlers": [
            {
                "method_name": spec.method_name,
                "subject": spec.subject,
                "partition_by": spec.partition_by,
                "ordering": spec.ordering,
                "idempotency": spec.idempotency,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in manifest.data_bus_handlers
        ],
        "authority_providers": [
            _authority_provider_spec_descriptor(spec)
            for spec in manifest.authority_providers
        ],
    }


def _manifest_to_descriptor_filtered(
        manifest: BundleInterfaceManifest,
        session: UserSession,
        props: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serialise a manifest filtered to the endpoint visibility rules for session.

    Visibility is checked against the **effective** user_types/roles (after
    bundle-props overrides have been applied).
    """
    effective_manifest = apply_bundle_overrides(manifest, props or {})
    visible_api_descriptors: list[Dict[str, Any]] = []
    for spec in manifest.api_endpoints:
        effective = apply_api_overrides(spec, props or {})
        auth = provider_surface_auth(
            props,
            "api",
            alias=effective.alias,
            http_method=effective.http_method,
            route=effective.route,
        )
        if is_api_enabled(props, effective) and _endpoint_visible(effective.user_types, effective.roles, session, auth):
            visible_api_descriptors.append(_api_spec_descriptor(spec, props))

    visible_widget_descriptors: list[Dict[str, Any]] = []
    for spec in manifest.ui_widgets:
        effective = apply_widget_overrides(spec, props or {})
        auth = provider_surface_auth(props, "widget", alias=effective.alias)
        if is_widget_enabled(props, effective) and _endpoint_visible(effective.user_types, effective.roles, session, auth):
            visible_widget_descriptors.append(_widget_spec_descriptor(spec, props))

    return {
        "enabled_path": canonical_enabled_path("bundle"),
        "allowed_roles": list(effective_manifest.allowed_roles),
        "apis": visible_api_descriptors,
        "mcp_endpoints": [
            _mcp_spec_descriptor(s, props)
            for s in manifest.mcp_endpoints
            if is_mcp_enabled(props, apply_mcp_overrides(s, props or {}))
        ],
        "widgets": visible_widget_descriptors,
        "on_message": manifest.on_message.method_name if manifest.on_message else None,
        "on_job": manifest.on_job.method_name if manifest.on_job else None,
        "scheduled_jobs": [_cron_spec_descriptor(s, props=props) for s in manifest.scheduled_jobs],
        "data_bus_handlers": [
            {
                "method_name": spec.method_name,
                "subject": spec.subject,
                "partition_by": spec.partition_by,
                "ordering": spec.ordering,
                "idempotency": spec.idempotency,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in manifest.data_bus_handlers
            if _endpoint_visible(spec.user_types, spec.roles, session)
        ],
        "authority_providers": [
            _authority_provider_spec_descriptor(spec)
            for spec in manifest.authority_providers
        ],
    }


@admin_router.get("/admin/integrations/bundles")
async def get_available_bundles(
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """Returns configured bundles for selection in the UI."""
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT
    try:
        redis = _get_app_redis(request)
        reg = await load_registry(redis, tenant_id, project_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Failed to load bundles registry for tenant/project")

    bundles_out = {}
    for bid, entry in reg.bundles.items():
        manifest = await _get_bundle_manifest(
            bundle_id=bid,
            tenant=tenant_id,
            project=project_id,
            request=request,
            session=session,
        )
        descriptor: Dict[str, Any] = {
            "id": bid,
            "name": entry.name,
            "description": entry.description,
            "path": entry.path,
            "module": entry.module,
            "singleton": bool(entry.singleton),
            "version": getattr(entry, "version", None),
            "repo": getattr(entry, "repo", None),
            "ref": getattr(entry, "ref", None),
            "subdir": getattr(entry, "subdir", None),
            "git_commit": getattr(entry, "git_commit", None),
        }
        if manifest is not None:
            props = _authoritative_bundle_props(tenant=tenant_id, project=project_id, bundle_id=bid)
            descriptor.update(_manifest_to_descriptor(manifest, props=props))
        bundles_out[bid] = descriptor

    return {
        "tenant": tenant_id,
        "project": project_id,
        "available_bundles": bundles_out,
        "default_bundle_id": reg.default_bundle_id,
        "authority": describe_authoritative_bundle_store(tenant_id, project_id),
    }


@admin_router.get("/admin/integrations/bundles/storage-registry")
async def get_bundle_storage_registry(
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(auth_without_pressure()),
):
    """Return active bundle registry fields used by storage operations."""
    del session
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT
    try:
        redis = _get_app_redis(request)
        reg = await load_registry(redis, tenant_id, project_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Failed to load bundles registry for tenant/project")
    summary = summarize_registry_bundles(reg.bundles, default_bundle_id=reg.default_bundle_id)
    return {
        "tenant": tenant_id,
        "project": project_id,
        **summary,
        "authority": describe_authoritative_bundle_store(tenant_id, project_id),
    }


@router.get("/bundles")
async def get_bundles(
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """
    Non-admin bundle listing for registered users.
    Returns bundle descriptors with apis/widgets/on_message/on_job filtered by the
    caller's roles. Origin fields (path, module, repo, ref, subdir, git_commit)
    are omitted.
    """
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT
    try:
        redis = _get_app_redis(request)
        reg = await load_registry(redis, tenant_id, project_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Failed to load bundles registry for tenant/project")

    bundles_out = {}
    for bid, entry in reg.bundles.items():
        manifest = await _get_bundle_manifest(
            bundle_id=bid,
            tenant=tenant_id,
            project=project_id,
            request=request,
            session=session,
        )
        props: Optional[Dict[str, Any]] = None
        if manifest is not None:
            props = _authoritative_bundle_props(tenant=tenant_id, project=project_id, bundle_id=bid)
            if not is_bundle_enabled(props):
                continue
        if not _bundle_allowed_for_session(manifest, session, props=props):
            continue
        descriptor: Dict[str, Any] = {
            "id": bid,
            "name": entry.name,
            "description": entry.description,
            "singleton": bool(entry.singleton),
            "version": getattr(entry, "version", None),
        }
        if manifest is not None:
            descriptor.update(_manifest_to_descriptor_filtered(manifest, session, props=props))
        bundles_out[bid] = descriptor

    return {
        "tenant": tenant_id,
        "project": project_id,
        "available_bundles": bundles_out,
        "default_bundle_id": reg.default_bundle_id,
    }


@admin_router.get("/admin/integrations/bundles/{bundle_id}/props")
async def get_bundle_props(
        bundle_id: str,
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT

    redis = _get_app_redis(request)
    props = await store_get_bundle_props(
        redis,
        tenant=tenant_id,
        project=project_id,
        bundle_id=bundle_id,
    )

    defaults_error: Optional[Dict[str, Any]] = None
    try:
        defaults = await _load_bundle_props_defaults(
            bundle_id=bundle_id,
            tenant=tenant_id,
            project=project_id,
            request=request,
            session=session,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "[bundle_props] Failed to load code defaults for bundle=%s tenant=%s project=%s",
            bundle_id,
            tenant_id,
            project_id,
            exc_info=True,
        )
        defaults = {}
        defaults_error = _bundle_defaults_error_payload(bundle_id, exc)
    if isinstance(defaults, dict) and "bundle_version" in defaults:
        props = dict(props)
        props["bundle_version"] = defaults.get("bundle_version")

    response = {
        "bundle_id": bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "props": props,
        "defaults": defaults,
    }
    if defaults_error:
        response["defaults_error"] = defaults_error
    return response


@admin_router.post("/admin/integrations/bundles/{bundle_id}/props", status_code=200)
async def set_bundle_props(
        bundle_id: str,
        payload: BundlePropsUpdateRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    redis = _get_app_redis(request)
    props = dict(payload.props or {})
    props.pop("bundle_version", None)

    try:
        if payload.op == "merge":
            await store_patch_bundle_props(
                redis,
                tenant=tenant_id,
                project=project_id,
                bundle_id=bundle_id,
                props_patch=props,
                actor=session.username or session.user_id or "unknown",
                source="admin",
            )
            return {"status": "ok", "bundle_id": bundle_id, "tenant": tenant_id, "project": project_id}
        elif payload.op != "replace":
            raise HTTPException(status_code=400, detail="Invalid op; use 'replace' or 'merge'")

        await store_put_bundle_props(
            redis,
            tenant=tenant_id,
            project=project_id,
            bundle_id=bundle_id,
            props=props,
            actor=session.username or session.user_id or "unknown",
            source="admin",
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve
    except HTTPException:
        raise

    return {"status": "ok", "bundle_id": bundle_id, "tenant": tenant_id, "project": project_id}


@admin_router.post("/admin/integrations/bundles/{bundle_id}/props/reset-code", status_code=200)
async def reset_bundle_props_from_code(
        bundle_id: str,
        payload: BundlePropsResetRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT

    try:
        defaults = await _load_bundle_props_defaults(
            bundle_id=bundle_id,
            tenant=tenant_id,
            project=project_id,
            request=request,
            session=session,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "[bundle_props] Reset from code failed while loading defaults for bundle=%s tenant=%s project=%s",
            bundle_id,
            tenant_id,
            project_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Failed to load bundle code defaults; persisted props were not changed.",
                "error": _bundle_defaults_error_payload(bundle_id, exc),
            },
        ) from exc

    redis = _get_app_redis(request)
    try:
        await store_put_bundle_props(
            redis,
            tenant=tenant_id,
            project=project_id,
            bundle_id=bundle_id,
            props=defaults,
            actor=session.username or session.user_id or "unknown",
            source="admin.reset-code",
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve

    return {"status": "ok", "bundle_id": bundle_id, "tenant": tenant_id, "project": project_id, "source": "code"}


@admin_router.post("/admin/integrations/bundles/{bundle_id}/secrets", status_code=200)
async def set_bundle_secrets(
        bundle_id: str,
        payload: BundleSecretsUpdateRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    try:
        secrets_manager = get_secrets_manager(settings)
    except SecretsManagerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not secrets_manager.can_write():
        raise HTTPException(status_code=503, detail="Secrets provider is not configured for writes")

    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    redis = _get_app_redis(request)

    mode = (payload.mode or "set").strip().lower()
    if mode not in {"set", "clear"}:
        raise HTTPException(status_code=400, detail="Invalid mode; use set or clear")

    flat: Dict[str, str] = {}
    keys: Set[str] = set()
    if mode == "set":
        _flatten_secrets(f"bundles.{bundle_id}.secrets", payload.secrets or {}, flat)
        keys = set(flat.keys())
    else:
        _flatten_secret_keys(f"bundles.{bundle_id}.secrets", payload.secrets or {}, keys)
        for key in keys:
            flat[key] = ""
    if not flat:
        return {"status": "ok", "bundle_id": bundle_id, "count": 0, "mode": mode}

    try:
        if mode == "set":
            await secrets_manager.set_many(flat)
        else:
            await secrets_manager.delete_many(keys)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store secrets: {exc}") from exc

    secrets_key = _bundle_secrets_key(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
    stored_keys: Set[str] = set()
    try:
        raw_keys = await redis.get(secrets_key)
        if raw_keys:
            stored_keys = set(json.loads(raw_keys))
    except Exception:
        stored_keys = set()
    if mode == "set":
        stored_keys.update(keys)
    else:
        stored_keys.difference_update(keys)

    metadata_key = f"bundles.{bundle_id}.secrets.__keys"
    try:
        if stored_keys:
            await secrets_manager.set_secret(
                metadata_key,
                json.dumps(sorted(stored_keys), ensure_ascii=False),
            )
        else:
            await secrets_manager.delete_secret(metadata_key)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store secrets metadata: {exc}") from exc

    try:
        await redis.set(secrets_key, json.dumps(sorted(stored_keys)))
    except Exception:
        pass

    await _publish_bundle_secrets_update(
        redis,
        tenant=tenant_id,
        project=project_id,
        bundle_id=bundle_id,
        scope="bundle",
        mode=mode,
        keys=keys,
        actor=session.username or session.user_id or "unknown",
    )

    return {
        "status": "ok",
        "bundle_id": bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "count": len(flat),
        "keys": sorted(keys),
        "stored_keys": sorted(stored_keys),
        "mode": mode,
    }


@admin_router.get("/admin/integrations/bundles/{bundle_id}/secrets")
async def get_bundle_secrets(
        bundle_id: str,
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT
    redis = _get_app_redis(request)
    secrets_key = _bundle_secrets_key(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
    keys: list[str] = []
    raw = await redis.get(secrets_key)
    if raw:
        try:
            keys = json.loads(raw)
        except Exception:
            keys = []
    if not keys:
        # Fallback: keys list stored in the configured secrets provider.
        raw_keys = await get_secret(f"bundles.{bundle_id}.secrets.__keys")
        if raw_keys:
            try:
                keys = json.loads(raw_keys) or []
                await redis.set(secrets_key, json.dumps(keys))
            except Exception:
                keys = []
    return {
        "bundle_id": bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "keys": keys or [],
    }


@router.post("/bundles/{tenant}/{project}/{bundle_id}/user-secrets", status_code=200)
async def set_current_user_bundle_secrets(
        tenant: str,
        project: str,
        bundle_id: str,
        payload: UserBundleSecretsUpdateRequest,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    user_id = str(session.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Current user id is unavailable for user secrets")

    settings = get_settings()
    try:
        secrets_manager = get_secrets_manager(settings)
    except SecretsManagerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not secrets_manager.can_write():
        raise HTTPException(status_code=503, detail="Secrets provider is not configured for writes")

    redis = _get_app_redis(request)
    mode = (payload.mode or "set").strip().lower()
    if mode not in {"set", "clear"}:
        raise HTTPException(status_code=400, detail="Invalid mode; use set or clear")

    flat: Dict[str, str] = {}
    keys: Set[str] = set()
    prefix_bundle_id = bundle_id
    if mode == "set":
        _flatten_secrets(
            f"users.{user_id}.bundles.{prefix_bundle_id}.secrets",
            payload.secrets or {},
            flat,
        )
        keys = set(flat.keys())
    else:
        _flatten_secret_keys(
            f"users.{user_id}.bundles.{prefix_bundle_id}.secrets",
            payload.secrets or {},
            keys,
        )
        for key in keys:
            flat[key] = ""
    if not flat:
        return {"status": "ok", "bundle_id": bundle_id, "user_id": user_id, "count": 0, "mode": mode}

    try:
        if mode == "set":
            await secrets_manager.set_many(flat)
        else:
            await secrets_manager.delete_many(keys)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store user secrets: {exc}") from exc

    secrets_key = _user_bundle_secrets_key(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        user_id=user_id,
    )
    stored_keys: Set[str] = set()
    try:
        raw_keys = await redis.get(secrets_key)
        if raw_keys:
            stored_keys = set(json.loads(raw_keys))
    except Exception:
        stored_keys = set()
    if mode == "set":
        stored_keys.update(keys)
    else:
        stored_keys.difference_update(keys)

    metadata_key = build_user_secret_metadata_key(user_id=user_id, bundle_id=bundle_id)
    try:
        if stored_keys:
            await secrets_manager.set_secret(
                metadata_key,
                json.dumps(sorted(stored_keys), ensure_ascii=False),
            )
        else:
            await secrets_manager.delete_secret(metadata_key)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store user secrets metadata: {exc}") from exc

    try:
        await redis.set(secrets_key, json.dumps(sorted(stored_keys)))
    except Exception:
        pass

    await _publish_bundle_secrets_update(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        scope="user",
        mode=mode,
        keys=keys,
        actor=session.username or session.user_id or "unknown",
        user_id=user_id,
    )

    return {
        "status": "ok",
        "bundle_id": bundle_id,
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        "count": len(flat),
        "mode": mode,
    }


@admin_router.post("/admin/integrations/bundles", status_code=200)
async def admin_set_bundles(
        payload: AdminBundlesUpdateRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    return await _do_set_bundles(payload, request, session)


@internal_router.post("/internal/bundles/update", status_code=200)
async def internal_set_bundles(payload: AdminBundlesUpdateRequest, request: Request):
    """
    Localhost-only bundle update endpoint for CI automation (ecs-bundles-update GA).
    Reachable only via ECS Exec (curl localhost:8020/internal/bundles/update).
    Refuses all connections that did not originate from 127.0.0.1 / ::1.
    """
    client_ip = request.client.host if request.client else ""
    if client_ip not in _LOCALHOST:
        raise HTTPException(status_code=403, detail="Internal endpoint: localhost only")
    # Delegate to the same logic used by the admin endpoint, synthesising
    # a minimal session so the audit field in the Redis message is meaningful.
    from kdcube_ai_app.auth.sessions import UserSession, UserType
    automation_session = UserSession(
        session_id="internal-automation",
        user_type=UserType.PRIVILEGED,
        user_id="ci-automation",
        username="ci-automation",
        roles=[],
        permissions=[],
    )
    return await _do_set_bundles(payload, request, automation_session)


@internal_router.post("/internal/bundles/status", status_code=200)
async def internal_bundle_status(payload: BundleStatusRequest, request: Request):
    """
    Localhost-only bundle status endpoint for CLI diagnostics.

    This is intentionally not a user-facing discovery API. It accepts one
    explicit bundle id, never lists other bundles, and is guarded by the same
    localhost-only check as the internal reload endpoint.
    """
    client_ip = request.client.host if request.client else ""
    if client_ip not in _LOCALHOST:
        raise HTTPException(status_code=403, detail="Internal endpoint: localhost only")

    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    bundle_id = str(payload.bundle_id or "").strip()
    if not bundle_id:
        raise HTTPException(status_code=400, detail="bundle_id is required")

    redis = _get_app_redis(request)
    try:
        reg = await load_registry(redis, tenant_id, project_id)
    except Exception as exc:
        return {
            "status": "error",
            "tenant": tenant_id,
            "project": project_id,
            "bundle_id": bundle_id,
            "declared": False,
            "loaded": False,
            "last_error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "where": "load_registry",
            },
            "authority": describe_authoritative_bundle_store(tenant_id, project_id),
        }

    entry = reg.bundles.get(bundle_id)
    if entry is None:
        return {
            "status": "ok",
            "tenant": tenant_id,
            "project": project_id,
            "bundle_id": bundle_id,
            "declared": False,
            "loaded": False,
            "authority": describe_authoritative_bundle_store(tenant_id, project_id),
        }

    entry_dict = entry.model_dump()
    spec = BundleSpec(
        path=entry.path,
        module=entry.module,
        singleton=bool(entry.singleton),
    )
    try:
        path_exists = bool(entry.path and Path(entry.path).exists())
    except Exception:
        path_exists = False

    cached_before = get_cached_manifest(spec) is not None
    try:
        manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
        props = _authoritative_bundle_props(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
        descriptor = _manifest_to_descriptor(manifest, props=props)
        return {
            "status": "ok",
            "tenant": tenant_id,
            "project": project_id,
            "bundle_id": bundle_id,
            "declared": True,
            "loaded": True,
            "cached_before": cached_before,
            "cached_after": get_cached_manifest(spec) is not None,
            "entry": entry_dict,
            "path_exists": path_exists,
            "interface": descriptor,
            "authority": describe_authoritative_bundle_store(tenant_id, project_id),
        }
    except Exception as exc:
        return {
            "status": "ok",
            "tenant": tenant_id,
            "project": project_id,
            "bundle_id": bundle_id,
            "declared": True,
            "loaded": False,
            "cached_before": cached_before,
            "cached_after": get_cached_manifest(spec) is not None,
            "entry": entry_dict,
            "path_exists": path_exists,
            "last_error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "where": "load_bundle_manifest",
                "traceback_tail": traceback.format_exc(limit=8),
            },
            "authority": describe_authoritative_bundle_store(tenant_id, project_id),
        }


async def _do_set_bundles(
        payload: AdminBundlesUpdateRequest,
        request: Request,
        session: UserSession,
):
    """Shared implementation for admin and internal bundle update endpoints."""
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    from kdcube_ai_app.infra.plugin.bundle_registry import (
        set_registry_async,
        upsert_bundles_async,
    )
    from kdcube_ai_app.infra.plugin.bundle_loader import clear_bundle_loader_caches
    from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids
    from kdcube_ai_app.infra.plugin.bundle_store import (
        load_registry as store_load,
        save_registry as store_save,
        apply_update as store_apply,
        _split_bundles_and_props as store_split_bundles_and_props,
        _apply_bundle_props as store_apply_bundle_props,
        _sync_bundle_props_authoritative as store_sync_bundle_props_authoritative,
    )

    redis = _get_app_redis(request)
    try:
        bundles_patch, props_map = store_split_bundles_and_props(payload.bundles or {})
        current = await store_load(redis, tenant_id, project_id)
        updated = store_apply(current, payload.op, bundles_patch, payload.default_bundle_id)
        await store_save(
            redis,
            updated,
            tenant_id,
            project_id,
            props_map=props_map,
            replace=(payload.op == "replace"),
        )
        if payload.op == "replace":
            await store_sync_bundle_props_authoritative(
                redis,
                tenant=tenant_id,
                project=project_id,
                props_map=props_map,
            )
        elif props_map:
            await store_apply_bundle_props(
                redis,
                tenant=tenant_id,
                project=project_id,
                props_map=props_map,
            )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        if payload.op == "replace":
            await set_registry_async(bundles_patch, payload.default_bundle_id)
        elif payload.op == "merge":
            await upsert_bundles_async(bundles_patch, payload.default_bundle_id)
        else:
            raise HTTPException(status_code=400, detail="Invalid op; use 'replace' or 'merge'")
        stopped_sidecars = stop_local_sidecars_for_bundle_ids(
            bundle_ids={str(bid).strip() for bid in (payload.bundles or {}).keys() if str(bid).strip()},
            tenant=tenant_id,
            project=project_id,
            terminate_timeout_sec=2.0,
            kill_timeout_sec=1.0,
        )
        if stopped_sidecars:
            logger.info(
                "[bundle.reload] stopped local sidecars for updated bundles: tenant=%s project=%s count=%s bundles=%s",
                tenant_id,
                project_id,
                stopped_sidecars,
                list((payload.bundles or {}).keys()),
            )
        reg = {bid: be.model_dump() for bid, be in updated.bundles.items()}
        default_id = updated.default_bundle_id
        clear_bundle_loader_caches()
    else:
        reg = {bid: be.model_dump() for bid, be in updated.bundles.items()}
        default_id = updated.default_bundle_id

    try:
        msg = {
            "type": "bundles.update",
            "op": payload.op,
            "bundles": bundles_patch,
            "default_bundle_id": payload.default_bundle_id,
            "tenant": tenant_id,
            "project": project_id,
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        await redis.publish(
            _bundles_channel(namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL, tenant=tenant_id, project=project_id),
            json.dumps(msg, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Failed to publish config update: %s", e)

    return {"status": "ok", "default_bundle_id": default_id, "count": len(reg)}


async def _do_reload_bundles_from_authority(
        request: Request,
        session: UserSession,
        payload: Optional[BundleReloadAuthorityRequest] = None,
):
    settings = get_settings()
    from kdcube_ai_app.infra.plugin.bundle_store import reload_registry_from_authority
    from kdcube_ai_app.infra.plugin.bundle_registry import set_registry_async
    from kdcube_ai_app.infra.plugin.bundle_loader import (
        BundleSpec,
        clear_bundle_loader_caches,
        evict_bundle_scope,
        invalidate_static_bundle_entrypoint_loads,
    )
    from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids

    tenant_id = (payload.tenant if payload else None) or settings.TENANT
    project_id = (payload.project if payload else None) or settings.PROJECT
    requested_bundle_id = str((payload.bundle_id if payload else "") or "").strip() or None
    redis = _get_app_redis(request)
    authority = describe_authoritative_bundle_store(tenant_id, project_id)
    logger.info(
        "[bundle.reload] requested: tenant=%s project=%s bundle=%s authority=%s pid=%s user=%s",
        tenant_id,
        project_id,
        requested_bundle_id or "<all>",
        authority,
        os.getpid(),
        session.username or session.user_id or "unknown",
    )

    try:
        reg = await reload_registry_from_authority(redis, tenant_id, project_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    target_entry = None
    if requested_bundle_id:
        target_entry = reg.bundles.get(requested_bundle_id)
        if target_entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"Bundle '{requested_bundle_id}' is not present in the active descriptor",
            )
        logger.info(
            "[bundle.reload] target resolved: tenant=%s project=%s bundle=%s path=%s module=%s singleton=%s pid=%s",
            tenant_id,
            project_id,
            requested_bundle_id,
            target_entry.path,
            target_entry.module,
            bool(target_entry.singleton),
            os.getpid(),
        )

    bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
    eviction_result: dict[str, int] | None = None
    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        await set_registry_async(bundles_dict, reg.default_bundle_id)
        target_bundle_ids = (
            {requested_bundle_id}
            if requested_bundle_id
            else {str(bid).strip() for bid in bundles_dict.keys() if str(bid).strip()}
        )
        stopped_sidecars = stop_local_sidecars_for_bundle_ids(
            bundle_ids=target_bundle_ids,
            tenant=tenant_id,
            project=project_id,
            terminate_timeout_sec=2.0,
            kill_timeout_sec=1.0,
        )
        if stopped_sidecars:
            logger.info(
                "[bundle.reload] stopped local sidecars during reload-authority: tenant=%s project=%s count=%s bundles=%s",
                tenant_id,
                project_id,
                stopped_sidecars,
                sorted(target_bundle_ids),
            )
        if target_entry is not None:
            target_payload = target_entry.model_dump()
            target_spec = BundleSpec(
                path=target_payload.get("path"),
                module=target_payload.get("module"),
                singleton=bool(target_payload.get("singleton")),
            )
            eviction_result = evict_bundle_scope(target_spec, drop_sys_modules=True)
            invalidate_static_bundle_entrypoint_loads(
                bundle_id=requested_bundle_id,
                tenant=tenant_id,
                project=project_id,
            )
            logger.info(
                "[bundle.reload] local bundle eviction complete: tenant=%s project=%s bundle=%s pid=%s eviction=%s",
                tenant_id,
                project_id,
                requested_bundle_id,
                os.getpid(),
                eviction_result,
            )
        else:
            clear_bundle_loader_caches()
            logger.info(
                "[bundle.reload] local cache clear complete: tenant=%s project=%s bundle=<all> pid=%s",
                tenant_id,
                project_id,
                os.getpid(),
            )

    changed_bundle_ids = (
        [requested_bundle_id]
        if requested_bundle_id
        else sorted(str(bid).strip() for bid in bundles_dict.keys() if str(bid).strip())
    )

    msg = {
        "type": "bundles.update",
        "op": "replace",
        "bundles": bundles_dict,
        "changed_bundle_ids": changed_bundle_ids,
        "default_bundle_id": reg.default_bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "updated_by": session.username or session.user_id or "unknown",
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    reload_channel = _bundles_channel(namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL, tenant=tenant_id, project=project_id)
    receivers = await redis.publish(
        reload_channel,
        json.dumps(msg, ensure_ascii=False),
    )
    logger.info(
        "[bundle.reload] broadcast sent: tenant=%s project=%s changed_bundles=%s channel=%s receivers=%s pid=%s",
        tenant_id,
        project_id,
        changed_bundle_ids,
        reload_channel,
        receivers,
        os.getpid(),
    )

    return {
        "status": "ok",
        "source": "authority",
        "default_bundle_id": reg.default_bundle_id,
        "count": len(reg.bundles),
        "bundle_id": requested_bundle_id,
        "authority": authority,
        "eviction": eviction_result,
        "changed_bundle_ids": changed_bundle_ids,
        "broadcast_receivers": receivers,
    }


@admin_router.post("/admin/integrations/bundles/reload-authority", status_code=200)
async def admin_reload_bundles_from_authority(
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
        payload: Optional[BundleReloadAuthorityRequest] = None,
):
    return await _do_reload_bundles_from_authority(request, session, payload)


@internal_router.post("/internal/bundles/reload-authority", status_code=200)
async def internal_reload_bundles_from_authority(payload: Optional[BundleReloadAuthorityRequest], request: Request):
    """
    Localhost-only bundle authority reload for local development / CLI automation.
    Re-applies the current bundles.yaml descriptor authority and clears bundle caches.
    """
    client_ip = request.client.host if request.client else ""
    if client_ip not in _LOCALHOST:
        raise HTTPException(status_code=403, detail="Internal endpoint: localhost only")
    from kdcube_ai_app.auth.sessions import UserSession, UserType
    automation_session = UserSession(
        session_id="internal-automation",
        user_type=UserType.PRIVILEGED,
        user_id="cli-local",
        username="cli-local",
        roles=[],
        permissions=[],
    )
    return await _do_reload_bundles_from_authority(request, automation_session, payload)


@admin_router.post("/admin/integrations/bundles/cleanup", status_code=200)
async def admin_cleanup_bundles(
        payload: BundleCleanupRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    from kdcube_ai_app.infra.plugin.bundle_loader import evict_inactive_specs, BundleSpec

    result = {"status": "ok"}
    redis = _get_app_redis(request)

    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        active_specs = []
        try:
            current = await load_registry(redis, tenant_id, project_id)
        except Exception:
            current = BundlesRegistry()
        for _bid, entry in (current.bundles or {}).items():
            try:
                active_specs.append(
                    BundleSpec(
                        path=entry.path,
                        module=entry.module,
                        singleton=bool(entry.singleton),
                    )
                )
            except Exception:
                continue

        result = evict_inactive_specs(
            active_specs=active_specs,
            drop_sys_modules=bool(payload.drop_sys_modules),
        )
        result["status"] = "ok"

    try:
        msg = {
            "type": "bundles.cleanup",
            "drop_sys_modules": bool(payload.drop_sys_modules),
            "tenant": tenant_id,
            "project": project_id,
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        await redis.publish(
            _bundles_channel(namespaces.CONFIG.BUNDLES.CLEANUP_CHANNEL, tenant=tenant_id, project=project_id),
            json.dumps(msg, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Failed to publish bundles cleanup: %s", e)

    return result


async def serve_static_asset(
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        path: str = "index.html",
        base_href: Optional[str] = None,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """
        Serve static assets built by BaseEntrypoint._ensure_ui_build().
        Files are read from <bundle_storage_root>/ui/<path>.

        URL: GET /api/integrations/static/{tenant}/{project}/{bundle_id}/{path}
        No authentication required — this endpoint serves a browser-facing SPA.
        """
    from fastapi.responses import FileResponse
    from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec

    tenant_id, project_id = _resolve_path_scope(tenant=tenant, project=project)
    spec = await _resolve_bundle_spec_from_runtime(
        request=request,
        tenant=tenant_id,
        project=project_id,
        bundle_id=bundle_id,
    )
    if not spec:
        raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' not found")

    storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
    ui_root = storage_root / "ui" if storage_root else None

    should_refresh_entrypoint = is_static_bundle_entrypoint_path(path)
    load_key = static_bundle_entrypoint_load_key(
        tenant=tenant_id,
        project=project_id,
        bundle_id=bundle_id,
        storage_root=storage_root,
    )
    if should_refresh_entrypoint:
        await run_static_bundle_entrypoint_load_once(
            load_key=load_key,
            load_coro_factory=lambda: _load_bundle_props_defaults(
                bundle_id=bundle_id,
                tenant=tenant_id,
                project=project_id,
                request=request,
                session=session,
                evict_before_load=False,
            ),
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" if storage_root else None

        # Signature-aware main-view rebuild on HTML-entrypoint hits.
        # `_load_bundle_props_defaults` warms the bundle (which runs
        # `on_bundle_load` → `_ensure_ui_build`) the first time; afterwards
        # the on-load coalescer would skip the build coro and a source
        # edit would never trigger a rebuild. The explicit signature-aware
        # call below sidesteps that: it consults the workflow's
        # `compute_ui_main_view_signature()` cheaply, and on a mismatch
        # falls through to the workflow's `_ensure_ui_build()`, which
        # in turn hits `run_once_for_shared_bundle_storage` for cross-
        # worker / cross-machine arbitration on EFS.
        if ui_root is not None:
            cached_workflow = peek_cached_singleton_for_spec(spec)
            if cached_workflow is not None and hasattr(cached_workflow, "_ensure_ui_build"):
                async def _ensure_main_view_ui_build_from_workflow() -> None:
                    ensure_ui_build = getattr(cached_workflow, "_ensure_ui_build", None)
                    if not callable(ensure_ui_build):
                        return
                    try:
                        maybe_result = ensure_ui_build()
                        if inspect.isawaitable(maybe_result):
                            await maybe_result
                    except HTTPException:
                        raise
                    except Exception as exc:
                        logger.exception(
                            "Bundle main-view UI build failed tenant=%s project=%s bundle=%s",
                            tenant_id,
                            project_id,
                            bundle_id,
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=f"Bundle '{bundle_id}' main-view UI build failed: {exc}",
                        ) from exc

                def _main_view_source_signature() -> Optional[str]:
                    compute = getattr(cached_workflow, "compute_ui_main_view_signature", None)
                    if not callable(compute):
                        return None
                    try:
                        return compute()
                    except Exception:
                        return None

                main_view_build_load_key = static_bundle_entrypoint_load_key(
                    tenant=tenant_id,
                    project=project_id,
                    bundle_id=f"{bundle_id}::main-view-build",
                    storage_root=storage_root,
                )
                await run_static_bundle_entrypoint_load_once(
                    load_key=main_view_build_load_key,
                    load_coro_factory=_ensure_main_view_ui_build_from_workflow,
                    signature_provider=_main_view_source_signature,
                )
                storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
                ui_root = storage_root / "ui" if storage_root else None

    if not ui_root or not ui_root.exists():
        # Build/refresh on HTML entrypoint requests. This triggers
        # on_bundle_load(), which calls BaseEntrypoint._ensure_ui_build() and
        # lets its signature cache decide whether a rebuild is necessary.
        fallback_load_key = static_bundle_entrypoint_load_key(
            tenant=tenant_id,
            project=project_id,
            bundle_id=f"{bundle_id}::main-ui-fallback",
            storage_root=storage_root,
        )
        await run_static_bundle_entrypoint_load_once(
            load_key=fallback_load_key,
            load_coro_factory=lambda: _load_bundle_props_defaults(
                bundle_id=bundle_id,
                tenant=tenant_id,
                project=project_id,
                request=request,
                session=session,
                evict_before_load=False,
            ),
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" if storage_root else None
        if not ui_root or not ui_root.exists():
            raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' does not have a UI defined")

    # Prevent path traversal
    try:
        target = (ui_root / path).resolve()
        target.relative_to(ui_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Directory → try index.html
    if target.is_dir():
        target = target / "index.html"

    # Missing file → SPA fallback (client-side routing)
    if not target.exists():
        target = ui_root / "index.html"
        if not target.exists():
            raise HTTPException(status_code=404, detail="Not found")

    # For index.html: inject <base> so that relative asset paths (./assets/...)
    # resolve correctly when the HTML is embedded via srcDoc in an iframe.
    if target.name == "index.html":
        from fastapi.responses import HTMLResponse
        resolved_base = base_href or f"/api/integrations/static/{tenant}/{project}/{bundle_id}/"
        content = target.read_text(encoding="utf-8")
        content = _inject_kdcube_resize_reporter(content, base_href=resolved_base)
        return HTMLResponse(content=content, headers={"Cache-Control": "no-cache"})

    rel_parts = target.relative_to(ui_root).parts
    headers = {"Cache-Control": "public, max-age=3600"}
    if rel_parts and rel_parts[0] == "assets":
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}

    return FileResponse(str(target), headers=headers)


@router.get("/static/{tenant}/{project}/{bundle_id}")
async def bundle_static_asset(
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    return await serve_static_asset(tenant=tenant, project=project, bundle_id=bundle_id, request=request,
                                    session=session)

@router.get("/static/{tenant}/{project}/{bundle_id}/{path:path}")
async def bundle_static_asset(
        tenant: str,
        project: str,
        bundle_id: str,
        path: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    return await serve_static_asset(tenant=tenant, project=project, bundle_id=bundle_id, path=path, request=request,
                                    session=session)


# Public (no-auth) main-UI shell, mirroring /public/widgets. Serves the built
# SPA to anonymous visitors so a bundle's main view can be embedded on a public
# page (e.g. a landing site). The shell is not sensitive; data is still gated by
# the bundle's authed APIs, and the app resolves identity itself via /profile.
@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/static")
async def bundle_static_asset_public(
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
):
    return await serve_static_asset(
        tenant=tenant, project=project, bundle_id=bundle_id, request=request,
        base_href=f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/static/",
        session=_build_public_api_request_session(request),
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/static/{path:path}")
async def bundle_static_asset_public_path(
        tenant: str,
        project: str,
        bundle_id: str,
        path: str,
        request: Request,
):
    return await serve_static_asset(
        tenant=tenant, project=project, bundle_id=bundle_id, path=path, request=request,
        base_href=f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/static/",
        session=_build_public_api_request_session(request),
    )


@router.post("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}")
async def call_bundle_op_public(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
):
    """
    Public (no authentication required) bundle operation endpoint.
    The bundle method must declare @api(route="public", ...) to be accessible.
    """
    payload, uploaded_files = await _parse_bundle_request_payload(request)
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=uploaded_files,
        request=request,
        operation=operation,
        route="public",
        session=_build_public_api_request_session(request),
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}")
async def call_bundle_op_public_get(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
):
    payload = BundleSuggestionsRequest()
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=[],
        request=request,
        operation=operation,
        route="public",
        session=_build_public_api_request_session(request),
    )


@router.post("/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}")
async def call_bundle_op(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """
    Load (or reuse singleton) bundle instance and call its operation (e.g. suggestions()).
    Returns generic JSON from the bundle.
    """
    payload, uploaded_files = await _parse_bundle_request_payload(request)
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=uploaded_files,
        request=request,
        operation=operation,
        route="operations",
        session=session,
    )


@router.post("/bundles/{tenant}/{project}/operations/{operation}")
async def call_bundle_op_default(
        tenant: str,
        project: str,
        operation: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    payload, uploaded_files = await _parse_bundle_request_payload(request)
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=None,
        payload=payload,
        uploaded_files=uploaded_files,
        request=request,
        operation=operation,
        route="operations",
        session=session,
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}")
async def get_bundle_interface(
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    payload = BundleSuggestionsRequest()
    workflow, spec_resolved, tenant_id, project_id, _comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=payload,
            request=request,
            session=session,
        )
    )
    manifest = discover_bundle_interface_manifest(workflow, bundle_id=spec_resolved.id)
    props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    workflow_props = _apply_rest_bundle_props_to_workflow(workflow=workflow, props=props)
    visible_widgets = _visible_widget_specs(manifest, session, props=workflow_props)
    visible_apis = _visible_api_specs(manifest, session, props=workflow_props)
    visible_mcp_endpoints = _visible_mcp_specs(manifest, session, props=workflow_props)
    visible_data_bus_handlers = [
        spec for spec in manifest.data_bus_handlers
        if _endpoint_visible(spec.user_types, spec.roles, session)
    ]
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "ui_widgets": [
            {
                "alias": spec.alias,
                "icon": spec.icon,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in visible_widgets
        ],
        "api_endpoints": [
            {
                "alias": spec.alias,
                "http_method": spec.http_method,
                "route": spec.route,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in visible_apis
        ],
        "mcp_endpoints": [
            {
                "alias": spec.alias,
                "route": spec.route,
                "transport": spec.transport,
            }
            for spec in visible_mcp_endpoints
        ],
        "ui_main": (
            {"method_name": manifest.ui_main.method_name}
            if manifest.ui_main
            else None
        ),
        "on_message": (
            {"method_name": manifest.on_message.method_name}
            if manifest.on_message
            else None
        ),
        "on_job": (
            {"method_name": manifest.on_job.method_name}
            if manifest.on_job
            else None
        ),
        "scheduled_jobs": [_cron_spec_descriptor(s, props=workflow_props) for s in manifest.scheduled_jobs],
        "data_bus_handlers": [
            {
                "subject": spec.subject,
                "partition_by": spec.partition_by,
                "ordering": spec.ordering,
                "idempotency": spec.idempotency,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in visible_data_bus_handlers
        ],
        "authority_providers": [
            _authority_provider_spec_descriptor(spec)
            for spec in manifest.authority_providers
        ],
    }


@router.get("/bundles/{tenant}/{project}/{bundle_id}/widgets")
async def list_bundle_widgets(
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    payload = BundleSuggestionsRequest()
    workflow, spec_resolved, tenant_id, project_id, _comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=payload,
            request=request,
            session=session,
        )
    )
    manifest = discover_bundle_interface_manifest(workflow, bundle_id=spec_resolved.id)
    props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    workflow_props = _apply_rest_bundle_props_to_workflow(workflow=workflow, props=props)
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "ui_widgets": [
            {
                "alias": spec.alias,
                "icon": spec.icon,
                "user_types": list(spec.user_types),
                "roles": list(spec.roles),
            }
            for spec in _visible_widget_specs(manifest, session, props=workflow_props)
        ],
    }


async def _fetch_bundle_widget_payload(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        request: Request,
        session: UserSession,
        widget_path: str = "",
):
    payload = BundleSuggestionsRequest()
    workflow, spec_resolved, tenant_id, project_id, comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=payload,
            request=request,
            session=session,
        )
    )
    manifest = discover_bundle_interface_manifest(workflow, bundle_id=spec_resolved.id)
    props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    workflow_props = _apply_rest_bundle_props_to_workflow(workflow=workflow, props=props)
    widget_spec = _resolve_widget_spec(
        manifest,
        alias=widget_alias,
    )
    if widget_spec is None:
        retry = await _reload_widget_manifest_after_miss(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            widget_alias=widget_alias,
            request=request,
            session=session,
            spec_resolved=spec_resolved,
            manifest=manifest,
        )
        if retry is not None:
            (
                workflow,
                spec_resolved,
                tenant_id,
                project_id,
                comm_context,
                manifest,
                props,
                workflow_props,
                widget_spec,
            ) = retry
    if widget_spec is None:
        _log_bundle_widget_lookup_mismatch(
            tenant_id=tenant_id,
            project_id=project_id,
            requested_bundle_id=bundle_id,
            spec_resolved=spec_resolved,
            widget_alias=widget_alias,
            workflow=workflow,
        )
        raise HTTPException(status_code=404, detail=f"Bundle does not define widget {widget_alias}")

    widget_spec = apply_widget_overrides(widget_spec, workflow_props)
    widget_auth = provider_surface_auth(workflow_props, "widget", alias=widget_spec.alias)
    if not _endpoint_visible(widget_spec.user_types, widget_spec.roles, session, widget_auth):
        raise HTTPException(status_code=403, detail=f"Bundle widget {widget_alias} is not visible to this user")
    if not is_bundle_enabled(workflow_props):
        raise HTTPException(status_code=404, detail=f"Bundle {spec_resolved.id} is disabled")
    if not is_widget_enabled(workflow_props, widget_spec):
        raise HTTPException(status_code=404, detail=f"Bundle widget {widget_alias} is not available")

    widget_static_cfg = None
    if not _static_widget_explicitly_disabled(workflow_props, widget_alias=widget_spec.alias):
        widget_static_cfg = _static_widget_config(workflow_props, widget_alias=widget_spec.alias)
    if widget_static_cfg:
        result = [_static_widget_iframe_html(
            tenant=tenant_id,
            project=project_id,
            bundle_id=spec_resolved.id,
            widget_alias=widget_spec.alias,
            widget_path=widget_path,
        )]
        return {
            "status": "ok",
            "tenant": tenant_id,
            "project": project_id,
            "bundle_id": spec_resolved.id,
            "widget": {
                "alias": widget_spec.alias,
                "icon": widget_spec.icon,
                "user_types": list(widget_spec.user_types),
                "roles": list(widget_spec.roles),
            },
            widget_alias: result,
        }

    try:
        fn = getattr(workflow, widget_spec.method_name)
        extra = _with_implicit_bundle_kwargs(
            _get_query_kwargs(request),
            user_id=session.user_id or session.fingerprint,
            fingerprint=session.fingerprint,
        )
        cleaned_widget_path = str(widget_path or "").lstrip("/")
        if cleaned_widget_path:
            extra.setdefault("widget_path", cleaned_widget_path)
            extra.setdefault("path", cleaned_widget_path)
        runtime_comm = _resolve_bound_runtime_comm(workflow=workflow, comm_context=comm_context)
        with bind_current_request_context(comm_context, comm=runtime_comm):
            result = await _invoke_bundle_callable(fn, **extra)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Bundle widget failed tenant=%s project=%s bundle=%s widget=%s method=%s",
            tenant_id,
            project_id,
            spec_resolved.id,
            widget_alias,
            widget_spec.method_name,
        )
        raise HTTPException(status_code=500, detail=f"{widget_alias}() failed: {e}")
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "widget": {
            "alias": widget_spec.alias,
            "icon": widget_spec.icon,
            "user_types": list(widget_spec.user_types),
            "roles": list(widget_spec.roles),
        },
        widget_alias: result,
    }


def _truthy_config_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() not in {"false", "disable", "disabled", "off", "0"}


def _log_bundle_widget_lookup_mismatch(
        *,
        tenant_id: str,
        project_id: str,
        requested_bundle_id: str | None,
        spec_resolved: Any,
        widget_alias: str,
        workflow: Any,
) -> None:
    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    cached_manifest = get_cached_manifest(spec)
    manifest = discover_bundle_interface_manifest(workflow, bundle_id=spec_resolved.id)
    raw_widget_members: list[dict[str, Any]] = []
    try:
        from kdcube_ai_app.infra.plugin.bundle_loader import UI_WIDGET_ATTR

        for name, member in inspect.getmembers(workflow.__class__, predicate=callable):
            attr = getattr(member, UI_WIDGET_ATTR, None)
            if attr is None:
                continue
            raw_widget_members.append(
                {
                    "method": name,
                    "alias": getattr(attr, "alias", None),
                    "attr_type": type(attr).__name__,
                    "attr_module": type(attr).__module__,
                    "attr_is_current_ui_widget_spec": isinstance(attr, UIWidgetSpec),
                }
            )
    except Exception:
        raw_widget_members = [{"error": traceback.format_exc(limit=2)}]
    logger.error(
        "Bundle widget lookup mismatch: tenant=%s project=%s requested_bundle=%s "
        "resolved_bundle=%s widget=%s path=%s module=%s singleton=%s "
        "workflow_class=%s workflow_module=%s workflow_file=%s "
        "manifest_widgets=%s cached_manifest_widgets=%s raw_widget_members=%s pid=%s",
        tenant_id,
        project_id,
        requested_bundle_id,
        spec_resolved.id,
        widget_alias,
        spec_resolved.path,
        spec_resolved.module,
        bool(spec_resolved.singleton),
        workflow.__class__.__name__,
        workflow.__class__.__module__,
        inspect.getsourcefile(workflow.__class__),
        [spec.alias for spec in manifest.ui_widgets],
        [spec.alias for spec in cached_manifest.ui_widgets] if cached_manifest is not None else None,
        raw_widget_members,
        os.getpid(),
    )


def _raw_static_widget_config(props: Dict[str, Any], *, widget_alias: str) -> Dict[str, Any] | None:
    ui_cfg = props.get("ui") if isinstance(props, dict) else {}
    if not isinstance(ui_cfg, dict):
        return None
    raw_widgets = ui_cfg.get("widgets")
    if not isinstance(raw_widgets, dict):
        return None
    cfg = raw_widgets.get(widget_alias)
    return cfg if isinstance(cfg, dict) else None


def _static_widget_explicitly_disabled(props: Dict[str, Any], *, widget_alias: str) -> bool:
    cfg = _raw_static_widget_config(props, widget_alias=widget_alias)
    return isinstance(cfg, dict) and "enabled" in cfg and not _truthy_config_value(cfg.get("enabled"))


def _static_widget_config(props: Dict[str, Any], *, widget_alias: str) -> Dict[str, Any] | None:
    cfg = _raw_static_widget_config(props, widget_alias=widget_alias)
    if not isinstance(cfg, dict):
        return None
    if "enabled" in cfg and not _truthy_config_value(cfg.get("enabled")):
        return None
    has_source = bool(str(cfg.get("src_folder") or cfg.get("source_dir") or "").strip())
    has_build = bool(str(cfg.get("build_command") or "").strip())
    if has_source and has_build:
        return cfg
    return None


def _static_widget_iframe_html(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        widget_path: str = "",
) -> str:
    cleaned_path = str(widget_path or "index.html").strip().lstrip("/") or "index.html"
    src = (
        f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}"
        f"/widgets/{widget_alias}/{cleaned_path}"
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}"
        "iframe{width:100%;height:100%;border:0;display:block}</style>"
        f"{_KDCUBE_RESIZE_REPORTER_SCRIPT}"
        "<script>"
        "(function(){"
        "var frame=null;"
        "var minTrustedViewportWidth=280;"
        "var seq=0;"
        "var debugEnabled=false;"
        "try{debugEnabled=/[?&]kdcube_resize_debug=1(?:&|$)/.test(window.location.search)||(window.localStorage&&window.localStorage.getItem('kdcube.resize.debug')==='1');}catch(e){}"
        "function debug(label,payload){if(!debugEnabled||!window.console||!console.debug){return;}console.debug('[kdcube-resize-wrapper]',label,payload||{});}"
        "function num(value){var n=Number(value);return isFinite(n)&&n>0?Math.ceil(n):0;}"
        "function ownSize(){"
        "var d=document.documentElement,b=document.body;"
        "var viewportWidth=Math.max(window.innerWidth||0,d?d.clientWidth:0,b?b.clientWidth:0);"
        "var contentWidth=Math.max(d?d.scrollWidth:0,d?d.offsetWidth:0,b?b.scrollWidth:0,b?b.offsetWidth:0);"
        "return {"
        "width:(contentWidth>viewportWidth+1)?contentWidth:0,"
        "contentWidth:contentWidth,"
        "viewportWidth:viewportWidth,"
        "trustedViewport:viewportWidth>=minTrustedViewportWidth,"
        "height:Math.max(d?d.scrollHeight:0,d?d.offsetHeight:0,d?d.clientHeight:0,b?b.scrollHeight:0,b?b.offsetHeight:0,b?b.clientHeight:0)"
        "};"
        "}"
        "function postResize(width,height){"
        "var size=ownSize();"
        "if(size.trustedViewport===false){debug('skip:untrusted-viewport',size);return;}"
        "var requestedWidth=Math.max(num(width),size.width);"
        "var message={type:'kdcube-resize',height:Math.max(num(height),size.height),width:requestedWidth,contentWidth:Math.max(requestedWidth,size.contentWidth),viewportWidth:size.viewportWidth,seq:++seq,reason:'static-widget-wrapper',minTrustedViewportWidth:minTrustedViewportWidth};"
        "debug('post',message);"
        "window.parent.postMessage(message,'*');"
        "}"
        "function applyChildSize(data){"
        "var h=num(data&&data.height),w=num(data&&data.width);"
        "if(h&&frame){frame.style.height=h+'px';}"
        "if(w&&frame){frame.style.minWidth=w+'px';}"
        "postResize(w,h);"
        "}"
        "function reportSameOriginFrame(){"
        "if(!frame){postResize(0,0);return;}"
        "try{"
        "var doc=frame.contentDocument||frame.contentWindow.document;"
        "var d=doc.documentElement,b=doc.body;"
        "var viewportWidth=Math.max(frame.clientWidth||0,d?d.clientWidth:0,b?b.clientWidth:0);"
        "var contentWidth=Math.max(d?d.scrollWidth:0,d?d.offsetWidth:0,b?b.scrollWidth:0,b?b.offsetWidth:0);"
        "applyChildSize({"
        "width:(contentWidth>viewportWidth+1)?contentWidth:0,"
        "height:Math.max(d?d.scrollHeight:0,d?d.offsetHeight:0,d?d.clientHeight:0,b?b.scrollHeight:0,b?b.offsetHeight:0,b?b.clientHeight:0)"
        "});"
        "}catch(e){postResize(0,0);}"
        "}"
        "window.addEventListener('DOMContentLoaded',function(){"
        "frame=document.getElementById('widget-frame');"
        "if(frame){frame.addEventListener('load',reportSameOriginFrame);}"
        "postResize(0,0);"
        "});"
        "window.addEventListener('resize',function(){postResize(0,0);},{passive:true});"
        "window.addEventListener('message',function(event){"
        "var data=event.data||{};"
        "if(data.type==='CONFIG_REQUEST'&&frame&&event.source===frame.contentWindow){window.parent.postMessage(data,'*');return;}"
        "if(data.type==='kdcube-resize'&&frame&&event.source===frame.contentWindow){applyChildSize(data);return;}"
        "if((data.type==='CONN_RESPONSE'||data.type==='CONFIG_RESPONSE')&&frame&&frame.contentWindow){frame.contentWindow.postMessage(data,'*');}"
        "});"
        "setTimeout(function(){postResize(0,0);},0);"
        "setTimeout(function(){postResize(0,0);},250);"
        "})();"
        "</script>"
        "</head><body><div id=\"root\">"
        f"<iframe id=\"widget-frame\" src=\"{html.escape(src, quote=True)}\" title=\"{html.escape(widget_alias, quote=True)}\"></iframe>"
        "</div></body></html>"
    )


async def _serve_static_widget_app(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        widget_path: str,
        request: Request,
        session: UserSession,
        public: bool = False,
):
    from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec

    payload = BundleSuggestionsRequest()
    workflow, spec_resolved, tenant_id, project_id, _comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=payload,
            request=request,
            session=session,
        )
    )
    manifest = discover_bundle_interface_manifest(workflow, bundle_id=spec_resolved.id)
    props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    workflow_props = _apply_rest_bundle_props_to_workflow(workflow=workflow, props=props)
    widget_spec = _resolve_widget_spec(
        manifest,
        alias=widget_alias,
    )
    if widget_spec is None:
        retry = await _reload_widget_manifest_after_miss(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            widget_alias=widget_alias,
            request=request,
            session=session,
            spec_resolved=spec_resolved,
            manifest=manifest,
        )
        if retry is not None:
            (
                workflow,
                spec_resolved,
                tenant_id,
                project_id,
                _comm_context,
                manifest,
                props,
                workflow_props,
                widget_spec,
            ) = retry
    if widget_spec is None:
        _log_bundle_widget_lookup_mismatch(
            tenant_id=tenant_id,
            project_id=project_id,
            requested_bundle_id=bundle_id,
            spec_resolved=spec_resolved,
            widget_alias=widget_alias,
            workflow=workflow,
        )
        raise HTTPException(status_code=404, detail=f"Bundle does not define widget {widget_alias}")

    widget_spec = apply_widget_overrides(widget_spec, workflow_props)
    widget_auth = provider_surface_auth(workflow_props, "widget", alias=widget_spec.alias)
    if not _endpoint_visible(widget_spec.user_types, widget_spec.roles, session, widget_auth):
        raise HTTPException(status_code=403, detail=f"Bundle widget {widget_alias} is not visible to this user")
    if not is_bundle_enabled(workflow_props):
        raise HTTPException(status_code=404, detail=f"Bundle {spec_resolved.id} is disabled")
    if not is_widget_enabled(workflow_props, widget_spec):
        raise HTTPException(status_code=404, detail=f"Bundle widget {widget_alias} is not available")
    widget_static_cfg = None
    if not _static_widget_explicitly_disabled(workflow_props, widget_alias=widget_spec.alias):
        widget_static_cfg = _static_widget_config(workflow_props, widget_alias=widget_spec.alias)
    if not widget_static_cfg:
        return None

    spec = spec_resolved

    storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
    ui_root = storage_root / "ui" / "widgets" / widget_spec.alias if storage_root else None
    cleaned_path = str(widget_path or "index.html").strip().lstrip("/") or "index.html"

    async def _ensure_widget_ui_build_from_workflow() -> None:
        ensure_ui_build = getattr(workflow, "_ensure_ui_build", None)
        if not callable(ensure_ui_build):
            return
        try:
            maybe_result = ensure_ui_build()
            if inspect.isawaitable(maybe_result):
                await maybe_result
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Bundle widget UI build failed tenant=%s project=%s bundle=%s widget=%s",
                tenant_id,
                project_id,
                spec_resolved.id,
                widget_spec.alias,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Bundle widget '{widget_alias}' UI build failed: {exc}",
            ) from exc

    def _widget_source_signature() -> Optional[str]:
        """Source-fingerprint provider for the widget UI build.

        Lets `run_static_bundle_entrypoint_load_once` short-circuit only when
        the cached signature matches the current source state — so a source
        edit on disk (e.g. someone touched `styles.css`) causes the next
        HTML-entrypoint request to rebuild, without requiring a manual
        `kdcube reload`. Returns `None` if the workflow can't compute the
        signature, in which case the legacy membership-based short-circuit
        applies.
        """
        compute = getattr(workflow, "compute_ui_widget_signature", None)
        if not callable(compute):
            return None
        try:
            return compute(widget_spec.alias)
        except Exception:
            return None

    should_refresh_entrypoint = is_static_bundle_entrypoint_path(cleaned_path)
    load_key = static_bundle_entrypoint_load_key(
        tenant=tenant_id,
        project=project_id,
        bundle_id=bundle_id,
        storage_root=storage_root,
    )
    build_load_key = static_bundle_entrypoint_load_key(
        tenant=tenant_id,
        project=project_id,
        bundle_id=f"{bundle_id}::widget-build::{widget_spec.alias}",
        storage_root=storage_root,
    )
    if should_refresh_entrypoint:
        await run_static_bundle_entrypoint_load_once(
            load_key=load_key,
            load_coro_factory=lambda: _load_bundle_props_defaults(
                bundle_id=bundle_id,
                tenant=tenant_id,
                project=project_id,
                request=request,
                session=session,
                evict_before_load=False,
            ),
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" / "widgets" / widget_spec.alias if storage_root else None

        # Always consult the widget build coordinator on HTML-entrypoint
        # requests, with a signature-aware short-circuit. When the source
        # tree is unchanged the call is a fast no-op (string equality on
        # the cached fingerprint). When the source tree changed since the
        # last successful build, we fall through to the build coro — which
        # in turn hits `run_once_for_shared_bundle_storage` for cross-
        # worker / cross-machine arbitration on EFS.
        await run_static_bundle_entrypoint_load_once(
            load_key=build_load_key,
            load_coro_factory=_ensure_widget_ui_build_from_workflow,
            signature_provider=_widget_source_signature,
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" / "widgets" / widget_spec.alias if storage_root else None

    if not ui_root or not ui_root.exists():
        # Cold-asset fallback: a static-asset request arrived before any
        # HTML-entrypoint request had a chance to build. Trigger the build
        # without a signature_provider so the legacy membership-based
        # short-circuit still keeps repeated asset hits cheap once the
        # build finishes.
        await run_static_bundle_entrypoint_load_once(
            load_key=build_load_key,
            load_coro_factory=_ensure_widget_ui_build_from_workflow,
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" / "widgets" / widget_spec.alias if storage_root else None

    if not ui_root or not ui_root.exists():
        fallback_load_key = static_bundle_entrypoint_load_key(
            tenant=tenant_id,
            project=project_id,
            bundle_id=f"{bundle_id}::widget-fallback::{widget_spec.alias}",
            storage_root=storage_root,
        )
        await run_static_bundle_entrypoint_load_once(
            load_key=fallback_load_key,
            load_coro_factory=lambda: _load_bundle_props_defaults(
                bundle_id=bundle_id,
                tenant=tenant_id,
                project=project_id,
                request=request,
                session=session,
                evict_before_load=False,
            ),
        )
        storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
        ui_root = storage_root / "ui" / "widgets" / widget_spec.alias if storage_root else None
        if not ui_root or not ui_root.exists():
            raise HTTPException(status_code=404, detail=f"Bundle widget '{widget_alias}' does not have a built UI")

    try:
        target = (ui_root / cleaned_path).resolve()
        target.relative_to(ui_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid widget path")

    if target.is_dir():
        target = target / "index.html"

    if not target.exists():
        target = ui_root / "index.html"
        if not target.exists():
            raise HTTPException(status_code=404, detail="Not found")

    if target.name == "index.html":
        base_route = "public/widgets" if public else "widgets"
        base_href = f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}/{base_route}/{widget_spec.alias}/"
        content = target.read_text(encoding="utf-8")
        content = _inject_kdcube_resize_reporter(content, base_href=base_href)
        return HTMLResponse(content=content, headers={"Cache-Control": "no-cache"})

    rel_parts = target.relative_to(ui_root).parts
    headers = {"Cache-Control": "public, max-age=3600"}
    if rel_parts and rel_parts[0] == "assets":
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}

    return FileResponse(str(target), headers=headers)


def _widget_payload_content(payload: Dict[str, Any], widget_alias: str) -> str:
    value = payload.get(widget_alias)
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _request_prefers_widget_html(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    return "text/html" in accept and "application/json" not in accept


@router.get("/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}")
async def fetch_bundle_widget(
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    if _request_prefers_widget_html(request):
        static_response = await _serve_static_widget_app(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            widget_alias=widget_alias,
            widget_path="index.html",
            request=request,
            session=session,
        )
        if static_response is not None:
            return static_response

    payload = await _fetch_bundle_widget_payload(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        widget_alias=widget_alias,
        request=request,
        session=session,
    )
    if _request_prefers_widget_html(request):
        return HTMLResponse(
            content=_inject_kdcube_resize_reporter(_widget_payload_content(payload, widget_alias)),
            headers={"Cache-Control": "no-cache"},
        )
    return payload


@router.get("/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}/{widget_path:path}")
async def serve_bundle_widget_path(
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        widget_path: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    static_response = await _serve_static_widget_app(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        widget_alias=widget_alias,
        widget_path=widget_path,
        request=request,
        session=session,
    )
    if static_response is not None:
        return static_response

    payload = await _fetch_bundle_widget_payload(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        widget_alias=widget_alias,
        widget_path=widget_path,
        request=request,
        session=session,
    )
    return HTMLResponse(
        content=_inject_kdcube_resize_reporter(_widget_payload_content(payload, widget_alias)),
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}")
async def fetch_public_static_bundle_widget(
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        request: Request,
):
    static_response = await _serve_static_widget_app(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        widget_alias=widget_alias,
        widget_path="index.html",
        request=request,
        session=_build_public_api_request_session(request),
        public=True,
    )
    if static_response is None:
        raise HTTPException(status_code=404, detail=f"Bundle widget {widget_alias} does not define a public static app")
    return static_response


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/widgets/{widget_alias}/{widget_path:path}")
async def serve_public_static_bundle_widget_path(
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        widget_path: str,
        request: Request,
):
    static_response = await _serve_static_widget_app(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        widget_alias=widget_alias,
        widget_path=widget_path,
        request=request,
        session=_build_public_api_request_session(request),
        public=True,
    )
    if static_response is None:
        raise HTTPException(status_code=404, detail=f"Bundle widget {widget_alias} does not define a public static app")
    return static_response


def _callable_accepts_kwarg(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except Exception:
        return False
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD or p.name == name
        for p in params
    )


async def _invoke_bundle_callable(fn: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    result = await asyncio.to_thread(fn, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _coerce_optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _build_mcp_request_session(request: Request) -> UserSession:
    settings = get_settings()
    auth_cfg = settings.AUTH
    runtime_cfg = settings.RUNTIME_CONFIG
    context = RequestContext(
        client_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", ""),
        authorization_header=request.headers.get("authorization"),
        id_token=(
            request.headers.get(auth_cfg.ID_TOKEN_HEADER_NAME)
            or request.headers.get(auth_cfg.ID_TOKEN_HEADER_NAME.lower())
        ),
        user_timezone=(
            request.headers.get(runtime_cfg.USER_TIMEZONE_HEADER_NAME)
            or request.headers.get(runtime_cfg.USER_TIMEZONE_HEADER_NAME.lower())
        ),
        user_utc_offset_min=_coerce_optional_int(
            request.headers.get(runtime_cfg.USER_UTC_OFFSET_MIN_HEADER_NAME)
            or request.headers.get(runtime_cfg.USER_UTC_OFFSET_MIN_HEADER_NAME.lower())
        ),
    )
    return UserSession(
        session_id=str(uuid.uuid4()),
        user_type=UserType.ANONYMOUS,
        fingerprint=context.get_fingerprint(),
        roles=[],
        permissions=[],
        timezone=context.user_timezone,
        request_context=context,
    )


def _build_public_api_request_session(request: Request) -> UserSession:
    return _build_mcp_request_session(request)


async def _call_bundle_mcp_limited(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        endpoint_alias: str,
        route: str,
        mcp_path: str,
        session: UserSession | None = None,
):
    resolved_session = session or _build_mcp_request_session(request)
    sem = _get_integrations_semaphore()
    if sem:
        async with sem:
            return await _call_bundle_mcp_inner(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
                request=request,
                endpoint_alias=endpoint_alias,
                route=route,
                mcp_path=mcp_path,
                session=resolved_session,
            )
    return await _call_bundle_mcp_inner(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        request=request,
        endpoint_alias=endpoint_alias,
        route=route,
        mcp_path=mcp_path,
        session=resolved_session,
    )


async def _call_bundle_mcp_inner(
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        request: Request,
        endpoint_alias: str,
        route: str,
        mcp_path: str,
        session: UserSession | None = None,
):
    resolved_session = session or _build_mcp_request_session(request)
    workflow, spec_resolved, tenant_id, project_id, comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=BundleSuggestionsRequest(),
            request=request,
            session=resolved_session,
        )
    )

    endpoint_spec = resolve_bundle_mcp_endpoint(
        workflow,
        alias=endpoint_alias,
        route=route,
        bundle_id=spec_resolved.id,
    )
    if endpoint_spec is None:
        raise HTTPException(status_code=404, detail=f"Bundle does not support MCP endpoint {endpoint_alias}")

    _props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    endpoint_spec = apply_mcp_overrides(endpoint_spec, _props)
    _apply_rest_bundle_props_to_workflow(workflow=workflow, props=_props)
    if not is_bundle_enabled(_props):
        raise HTTPException(status_code=404, detail=f"Bundle {spec_resolved.id} is disabled")
    if not is_mcp_enabled(_props, endpoint_spec):
        raise HTTPException(status_code=404, detail=f"Bundle MCP endpoint {endpoint_alias} is not available")

    mcp_request_body: bytes | None = None
    if mcp_auth_mode(endpoint_spec.auth) == "managed":
        mcp_request_body = await request.body()
        denial = await authorize_delegated_mcp_request(
            request=request,
            body=mcp_request_body,
            auth=endpoint_spec.auth,
        )
        if denial is not None:
            return denial

    try:
        fn = getattr(workflow, endpoint_spec.method_name)
        extra: Dict[str, Any] = {}
        if _callable_accepts_kwarg(fn, "request"):
            extra["request"] = request
        if _callable_accepts_kwarg(fn, "alias"):
            extra["alias"] = endpoint_alias
        if _callable_accepts_kwarg(fn, "mcp_path"):
            extra["mcp_path"] = mcp_path
        runtime_comm = _resolve_bound_runtime_comm(workflow=workflow, comm_context=comm_context)
        with bind_current_request_context(comm_context, comm=runtime_comm):
            result = await _invoke_bundle_callable(fn, **extra)
            mcp_app = _coerce_bundle_mcp_asgi_app(result, transport=endpoint_spec.transport)
            return await _dispatch_bundle_mcp_request(
                request=request,
                mcp_app=mcp_app,
                transport=endpoint_spec.transport,
                mcp_path=mcp_path,
                body=mcp_request_body,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Bundle MCP endpoint failed tenant=%s project=%s bundle=%s route=%s endpoint=%s method=%s",
            tenant_id,
            project_id,
            spec_resolved.id,
            route,
            endpoint_alias,
            endpoint_spec.method_name,
        )
        raise HTTPException(status_code=500, detail=f"mcp/{endpoint_alias} failed: {e}")


@router.api_route(
    "/bundles/{tenant}/{project}/{bundle_id}/mcp/{endpoint_alias}",
    methods=["GET", "POST"],
)
@router.api_route(
    "/bundles/{tenant}/{project}/{bundle_id}/mcp/{endpoint_alias}/{mcp_path:path}",
    methods=["GET", "POST"],
)
async def call_bundle_mcp(
        tenant: str,
        project: str,
        bundle_id: str,
        endpoint_alias: str,
        request: Request,
        mcp_path: str = "",
):
    return await _call_bundle_mcp_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        request=request,
        endpoint_alias=endpoint_alias,
        route="operations",
        mcp_path=mcp_path,
    )


@router.api_route(
    "/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{endpoint_alias}",
    methods=["GET", "POST"],
)
@router.api_route(
    "/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{endpoint_alias}/{mcp_path:path}",
    methods=["GET", "POST"],
)
async def call_bundle_mcp_public(
        tenant: str,
        project: str,
        bundle_id: str,
        endpoint_alias: str,
        request: Request,
        mcp_path: str = "",
):
    return await _call_bundle_mcp_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        request=request,
        endpoint_alias=endpoint_alias,
        route="public",
        mcp_path=mcp_path,
    )


# Generic public op route with a trailing sub-path: an op declared
# @api(route="public", ...) receives it as `path_tail` if it accepts that kwarg
# (otherwise the segment is ignored). Registered AFTER the concrete public
# sub-surfaces (static / widgets / mcp) so first-match keeps those routes;
# this catch-all only handles whatever they did not claim.
@router.post("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}/{path_tail:path}")
async def call_bundle_op_public_with_path(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        path_tail: str,
        request: Request,
):
    payload, uploaded_files = await _parse_bundle_request_payload(request)
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=uploaded_files,
        request=request,
        operation=operation,
        route="public",
        session=_build_public_api_request_session(request),
        path_tail=path_tail,
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}/{path_tail:path}")
async def call_bundle_op_public_get_with_path(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        path_tail: str,
        request: Request,
):
    payload = BundleSuggestionsRequest()
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=[],
        request=request,
        operation=operation,
        route="public",
        session=_build_public_api_request_session(request),
        path_tail=path_tail,
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}")
async def call_bundle_op_get(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    payload = BundleSuggestionsRequest()
    return await _call_bundle_op_limited(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=[],
        request=request,
        operation=operation,
        route="operations",
        session=session,
    )


async def _call_bundle_op_limited(
        *,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
        uploaded_files: Optional[List[BundleUploadedFile]] = None,
        request: Request,
        operation: str,
        route: str,
        session: UserSession,
        path_tail: Optional[str] = None,
):
    uploaded_files = list(uploaded_files or [])
    sem = _get_integrations_semaphore()
    if sem:
        async with sem:
            return await _call_bundle_op_inner(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
                payload=payload,
                uploaded_files=uploaded_files,
                request=request,
                operation=operation,
                route=route,
                session=session,
                path_tail=path_tail,
            )
    return await _call_bundle_op_inner(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        payload=payload,
        uploaded_files=uploaded_files,
        request=request,
        operation=operation,
        route=route,
        session=session,
        path_tail=path_tail,
    )


async def _resolve_requested_bundle_id(
        *,
        path_bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
        request: Request,
        tenant: str,
        project: str,
) -> str:
    if path_bundle_id and str(path_bundle_id).strip():
        return str(path_bundle_id).strip()

    payload_bundle_id = str(payload.bundle_id or "").strip()
    if payload_bundle_id:
        return payload_bundle_id

    cfg_bundle_id = str(getattr(payload.config_request, "agentic_bundle_id", "") or "").strip()
    if cfg_bundle_id:
        return cfg_bundle_id

    reg = await load_registry(_get_app_redis(request), tenant, project)
    default_bundle_id = str(reg.default_bundle_id or "").strip()
    if default_bundle_id:
        return default_bundle_id

    raise HTTPException(status_code=404, detail="No bundle_id provided and no default bundle is configured")


def _get_query_kwargs(request: Request) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in request.query_params.keys():
        values = request.query_params.getlist(key)
        if not values:
            continue
        out[key] = values[0] if len(values) == 1 else values
    return out


async def _load_bundle_workflow(
        *,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
        request: Request,
        session: UserSession,
) -> tuple[Any, Any, str, str, ExternalEventPayload]:
    cfg_req = payload.config_request or ConfigRequest()
    tenant_id, project_id = _resolve_path_scope(tenant=tenant, project=project)
    _bind_route_scope_to_config_request(cfg_req, tenant=tenant_id, project=project_id)

    requested_bundle_id = await _resolve_requested_bundle_id(
        path_bundle_id=bundle_id,
        payload=payload,
        request=request,
        tenant=tenant_id,
        project=project_id,
    )
    cfg_req.agentic_bundle_id = requested_bundle_id
    cfg_req = await resolve_config_request_secrets(cfg_req, bundle_id=requested_bundle_id)
    request_id = str(uuid.uuid4())

    spec_resolved = await _resolve_bundle_spec_from_runtime(
        request=request,
        tenant=tenant_id,
        project=project_id,
        bundle_id=cfg_req.agentic_bundle_id,
    )
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {cfg_req.agentic_bundle_id} not found")

    wf_config = create_workflow_config(cfg_req)
    wf_config.ai_bundle_spec = spec_resolved

    spec = BundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    routing = _build_rest_bundle_routing(
        request=request,
        session_id=session.session_id,
        bundle_id=spec_resolved.id,
    )
    comm_context = ExternalEventPayload(
        request=ExternalEventRequest(request_id=request_id),
        routing=routing,
        actor=ExternalEventActor(
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        user=ExternalEventUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            email=session.email,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.request_context.user_timezone,
            utc_offset_min=session.request_context.user_utc_offset_min,
            identity_authority=dict(getattr(session, "identity_authority", None) or {}),
        ),
    )

    redis = _get_app_redis(request)
    pg_pool = _get_app_pg_pool(request)
    try:
        workflow, _mod = await get_workflow_instance_async(
            spec, wf_config, comm_context=comm_context, redis=redis, pg_pool=pg_pool,
        )
    except Exception as e:
        logger.exception(
            "[call_bundle_op.%s.%s] Failed to load requested bundle id=%s spec=%s",
            tenant,
            project,
            spec_resolved.id,
            asdict(spec),
        )
        raise HTTPException(status_code=500, detail=f"Failed to load bundle {spec_resolved.id}: {e}") from e

    _bind_proc_runtime_services_to_workflow(workflow=workflow, request=request)
    return workflow, spec_resolved, tenant_id, project_id, comm_context


async def _call_bundle_op_inner(
        *,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
        uploaded_files: Optional[List[BundleUploadedFile]] = None,
        request: Request,
        operation: str,
        route: str,
        session: UserSession,
        path_tail: Optional[str] = None,
        method_override: Optional[str] = None,
):
    uploaded_files = list(uploaded_files or [])
    workflow, spec_resolved, tenant_id, project_id, comm_context = _unpack_loaded_bundle_workflow(
        await _load_bundle_workflow(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            payload=payload,
            request=request,
            session=session,
        )
    )

    request_method = str(method_override or getattr(request, "method", "POST") or "POST").upper()
    endpoint_spec, allowed_methods = resolve_bundle_api_endpoint(
        workflow,
        alias=operation,
        http_method=request_method,
        route=route,
        bundle_id=spec_resolved.id,
    )
    if endpoint_spec is None:
        if allowed_methods:
            raise HTTPException(
                status_code=405,
                detail=f"Bundle operation {operation} does not support {request_method}. Allowed: {', '.join(allowed_methods)}",
            )
        raise HTTPException(status_code=404, detail=f"Bundle does not support operation {operation}")
    _props = _authoritative_bundle_props(
        tenant=tenant_id,
        project=project_id,
        bundle_id=spec_resolved.id,
    )
    endpoint_spec = apply_api_overrides(endpoint_spec, _props)
    endpoint_auth = provider_surface_auth(
        _props,
        "api",
        alias=endpoint_spec.alias,
        http_method=endpoint_spec.http_method,
        route=endpoint_spec.route,
    )
    if not _endpoint_visible(endpoint_spec.user_types, endpoint_spec.roles, session, endpoint_auth):
        raise HTTPException(status_code=403, detail=f"Bundle operation {operation} is not visible to this user")
    _apply_rest_bundle_props_to_workflow(workflow=workflow, props=_props)
    if not is_bundle_enabled(_props):
        raise HTTPException(status_code=404, detail=f"Bundle {spec_resolved.id} is disabled")
    if not is_api_enabled(_props, endpoint_spec):
        raise HTTPException(status_code=404, detail=f"Bundle operation {operation} is not available")

    try:
        fn = getattr(workflow, endpoint_spec.method_name)
        extra = payload.data or {}
        if request_method == "GET":
            extra = _get_query_kwargs(request)
        elif uploaded_files:
            extra = dict(extra or {})
            extra["uploaded_files"] = uploaded_files
        if _callable_accepts_kwarg(fn, "request"):
            extra = dict(extra or {})
            extra["request"] = request
        if path_tail and _callable_accepts_kwarg(fn, "path_tail"):
            extra = dict(extra or {})
            extra["path_tail"] = path_tail
        extra = _with_implicit_bundle_kwargs(
            extra,
            user_id=session.user_id or session.fingerprint,
            fingerprint=session.fingerprint,
        )
        runtime_comm = _resolve_bound_runtime_comm(workflow=workflow, comm_context=comm_context)

        async def _call_peer_bundle_operation(call: BundleOperationCall) -> Mapping[str, Any]:
            return await _call_bundle_op_inner(
                tenant=call.tenant or tenant_id,
                project=call.project or project_id,
                bundle_id=call.bundle_id,
                payload=BundleSuggestionsRequest(
                    conversation_id=payload.conversation_id,
                    data=dict(call.data or {}),
                ),
                uploaded_files=[],
                request=request,
                operation=call.operation,
                route=call.route or "operations",
                session=session,
                method_override=call.http_method or "POST",
            )

        peer_redis = _get_app_redis(request)

        async def _call_peer_bundle_operation_stream(call: BundleOperationStreamCall) -> BundleOperationStreamResult:
            return await invoke_local_bundle_operation_stream(
                call,
                comm_context=comm_context,
                redis=peer_redis,
                pg_pool=_get_app_pg_pool(request),
            )

        async def _call_peer_bundle_named_service(call: BundleNamedServiceCall) -> BundleNamedServiceResult:
            return await invoke_local_bundle_named_service(
                call,
                comm_context=comm_context,
                redis=peer_redis,
                pg_pool=_get_app_pg_pool(request),
            )

        with (
            bind_current_request_context(comm_context, comm=runtime_comm),
            bind_named_service_discovery(
                RedisNamedServiceDiscovery(peer_redis, tenant=tenant_id, project=project_id)
            ),
            bind_bundle_named_service_caller(_call_peer_bundle_named_service),
            bind_bundle_operation_caller(_call_peer_bundle_operation),
            bind_bundle_operation_stream_caller(_call_peer_bundle_operation_stream),
        ):
            result = await _invoke_bundle_callable(fn, **extra)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Bundle operation failed tenant=%s project=%s bundle=%s route=%s method=%s operation=%s endpoint=%s",
            tenant_id,
            project_id,
            spec_resolved.id,
            route,
            request_method,
            operation,
            endpoint_spec.method_name,
        )
        raise HTTPException(status_code=500, detail=f"{operation}() failed: {e}")

    http_response = _coerce_bundle_http_response(result)
    if http_response is not None:
        return http_response

    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "conversation_id": payload.conversation_id,
        operation: result,
    }
