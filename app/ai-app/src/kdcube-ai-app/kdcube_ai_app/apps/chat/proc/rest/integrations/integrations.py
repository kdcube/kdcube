# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/proc/rest/integrations/integrations.py
import asyncio
import hmac
import inspect
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional, Dict, Any, Set, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

from kdcube_ai_app.apps.chat.emitters import build_comm_from_comm_context
from kdcube_ai_app.apps.chat.ingress.resolvers import require_auth, auth_without_pressure, get_user_session_dependency
from kdcube_ai_app.apps.middleware.gateway import STATE_STREAM_ID, extract_stream_id
from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskPayload,
    ChatTaskRouting,
    ChatTaskActor,
    ChatTaskUser,
    ChatTaskRequest,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import (
    BundleBinaryResponse,
    BundleFileResponse,
    BundleUploadedFile,
)
from kdcube_ai_app.infra.plugin.bundle_registry import (
    resolve_bundle_async,
    get_all,
    get_default_id,
)
from kdcube_ai_app.infra.plugin.bundle_store import (
    load_registry,
    BundlesRegistry,
    BundleEntry,
)
from kdcube_ai_app.infra.plugin.agentic_loader import (
    AgenticBundleSpec,
    APIEndpointSpec,
    BundleInterfaceManifest,
    UIWidgetSpec,
    cache_key_for_spec,
    discover_bundle_interface_manifest,
    get_cached_manifest,
    get_workflow_instance,
    load_bundle_manifest,
    resolve_bundle_api_endpoint,
    resolve_bundle_widget,
)
from kdcube_ai_app.infra.secrets import (
    SecretsManagerError,
    SecretsManagerWriteError,
    build_user_secret_metadata_key,
    get_secrets_manager,
)
import kdcube_ai_app.infra.namespaces as namespaces

logger = logging.getLogger("ChatProc.Integrations")
_integrations_limit: Optional[int] = None
_integrations_semaphore = None


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
        # fallback to router state if wired via mount_integrations_routers
        redis = getattr(router.state, "redis_async", None)
    if redis is None:
        redis = getattr(admin_router.state, "redis_async", None)
    if redis is None:
        redis = getattr(internal_router.state, "redis_async", None)
    if redis is None:
        raise RuntimeError("redis_async is not initialized on app.state")
    return redis


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


def _build_rest_bundle_routing(*, request: Request, session_id: str, bundle_id: str) -> ChatTaskRouting:
    return ChatTaskRouting(
        session_id=session_id,
        bundle_id=bundle_id,
        socket_id=_request_stream_id(request),
    )


def _unpack_loaded_bundle_workflow(result: tuple[Any, ...]) -> tuple[Any, Any, str, str, Optional[ChatTaskPayload]]:
    if len(result) == 5:
        workflow, spec_resolved, tenant_id, project_id, comm_context = result
        return workflow, spec_resolved, tenant_id, project_id, comm_context
    if len(result) == 4:
        workflow, spec_resolved, tenant_id, project_id = result
        return workflow, spec_resolved, tenant_id, project_id, None
    raise ValueError(f"Unexpected _load_bundle_workflow result shape: {len(result)}")


def _resolve_bound_runtime_comm(*, workflow: Any, comm_context: Optional[ChatTaskPayload]):
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


def _session_role_names(session: UserSession) -> set[str]:
    names: set[str] = set()
    user_type = str(getattr(getattr(session, "user_type", None), "value", "") or "").strip()
    if user_type:
        names.add(user_type)
    for item in getattr(session, "roles", None) or []:
        text = str(item or "").strip()
        if text:
            names.add(text)
    return names


def _roles_visible(required_roles: tuple[str, ...] | list[str] | None, session: UserSession) -> bool:
    roles = tuple(str(role or "").strip() for role in (required_roles or ()) if str(role or "").strip())
    if not roles:
        return True
    if "super-admin" in roles and "kdcube:role:super-admin" in _session_role_names(session):
        return True
    current = _session_role_names(session)
    return any(role in current for role in roles)


def _visible_widget_specs(manifest: BundleInterfaceManifest, session: UserSession) -> list[UIWidgetSpec]:
    return [spec for spec in manifest.ui_widgets if _roles_visible(spec.roles, session)]


def _visible_api_specs(manifest: BundleInterfaceManifest, session: UserSession) -> list[APIEndpointSpec]:
    return [spec for spec in manifest.api_endpoints if _roles_visible(spec.roles, session)]


def _user_raw_roles(session: UserSession) -> set[str]:
    """Raw (externally defined) roles: kdcube:role:* entries from session.roles."""
    return {
        r for r in (session.roles or [])
        if isinstance(r, str) and r.startswith("kdcube:role:")
    }


def _bundle_allowed_for_session(manifest: "BundleInterfaceManifest | None", session: UserSession) -> bool:
    """Bundle-level access check based on allowed_roles declared on @agentic_workflow.
    No allowed_roles (empty) means the bundle is visible to all authenticated users."""
    if manifest is None or not manifest.allowed_roles:
        return True
    return bool(_user_raw_roles(session) & set(manifest.allowed_roles))


router = APIRouter()
admin_router = APIRouter()
internal_router = APIRouter()

_LOCALHOST = {"127.0.0.1", "::1"}


class BundleSuggestionsRequest(BaseModel):
    conversation_id: Optional[str] = None
    bundle_id: Optional[str] = None
    config_request: Optional[ConfigRequest] = None
    data: Optional[Dict[str, Any]] = None


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
    return None


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
        return BundleSuggestionsRequest.model_validate(raw_body), []

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


class BundleResetEnvRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None
    bundle_id: Optional[str] = None


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


async def _load_bundle_props_defaults(
        *,
        bundle_id: str,
        tenant: str,
        project: str,
        request: Request,
        session: UserSession,
) -> Dict[str, Any]:
    spec_resolved = await resolve_bundle_async(bundle_id, override=None)
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {bundle_id} not found")

    try:
        wf_config = create_workflow_config(ConfigRequest())
    except Exception:
        wf_config = create_workflow_config(ConfigRequest.model_validate({"project": project}))

    spec = AgenticBundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    # Always reload bundle code for "code defaults" so UI reflects latest code.
    try:
        from kdcube_ai_app.infra.plugin.agentic_loader import evict_spec
        evict_spec(spec)
    except Exception:
        pass
    routing = _build_rest_bundle_routing(
        request=request,
        session_id=session.session_id,
        bundle_id=spec_resolved.id,
    )
    comm_context = ChatTaskPayload(
        request=ChatTaskRequest(request_id=str(uuid.uuid4())),
        routing=routing,
        actor=ChatTaskActor(
            tenant_id=tenant,
            project_id=project,
        ),
        user=ChatTaskUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.request_context.user_timezone,
            utc_offset_min=session.request_context.user_utc_offset_min,
        ),
    )

    wf_config.ai_bundle_spec = spec_resolved
    redis = _get_app_redis(request)
    workflow, _mod = get_workflow_instance(
        spec, wf_config, comm_context=comm_context, redis=redis,
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
    spec_resolved = await resolve_bundle_async(bundle_id, override=None)
    if not spec_resolved:
        return None
    spec = AgenticBundleSpec(
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


def _manifest_to_descriptor(manifest: BundleInterfaceManifest) -> Dict[str, Any]:
    """Serialise a full (unfiltered) manifest to a plain dict."""
    return {
        "apis": [
            {"alias": s.alias, "http_method": s.http_method, "route": s.route, "roles": list(s.roles)}
            for s in manifest.api_endpoints
        ],
        "widgets": [
            {"alias": s.alias, "icon": s.icon, "roles": list(s.roles)}
            for s in manifest.ui_widgets
        ],
        "on_message": manifest.on_message.method_name if manifest.on_message else None,
        "scheduled_jobs": [
            {
                "method_name": s.method_name,
                "alias": s.alias,
                "cron_expression": s.cron_expression,
                "expr_config": s.expr_config,
                "span": s.span,
            }
            for s in manifest.scheduled_jobs
        ],
    }


def _manifest_to_descriptor_filtered(
        manifest: BundleInterfaceManifest,
        session: UserSession,
) -> Dict[str, Any]:
    """Serialise a manifest filtered to the roles visible to session."""
    return {
        "apis": [
            {"alias": s.alias, "http_method": s.http_method, "route": s.route, "roles": list(s.roles)}
            for s in manifest.api_endpoints
            if _roles_visible(s.roles, session)
        ],
        "widgets": [
            {"alias": s.alias, "icon": s.icon, "roles": list(s.roles)}
            for s in manifest.ui_widgets
            if _roles_visible(s.roles, session)
        ],
        "on_message": manifest.on_message.method_name if manifest.on_message else None,
        "scheduled_jobs": [
            {
                "method_name": s.method_name,
                "alias": s.alias,
                "cron_expression": s.cron_expression,
                "expr_config": s.expr_config,
                "span": s.span,
            }
            for s in manifest.scheduled_jobs
        ],
    }


@admin_router.get("/admin/integrations/bundles")
async def get_available_bundles(
        request: Request,
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        session: UserSession = Depends(require_auth(RequireUser())),
):
    """
    Returns configured bundles for selection in the UI.
    Read from Redis (source of truth), fallback to in-memory if needed.
    """
    settings = get_settings()
    tenant_id = tenant or settings.TENANT
    project_id = project or settings.PROJECT
    try:
        redis = _get_app_redis(request)
        reg = await load_registry(redis, tenant_id, project_id)
    except Exception:
        if tenant_id == settings.TENANT and project_id == settings.PROJECT:
            reg = BundlesRegistry(
                default_bundle_id=get_default_id(),
                bundles={bid: BundleEntry(**info) for bid, info in get_all().items()},
            )
        else:
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
            descriptor.update(_manifest_to_descriptor(manifest))
        bundles_out[bid] = descriptor

    return {
        "tenant": tenant_id,
        "project": project_id,
        "available_bundles": bundles_out,
        "default_bundle_id": reg.default_bundle_id,
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
    Returns bundle descriptors with apis/widgets/on_message filtered by the
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
        if tenant_id == settings.TENANT and project_id == settings.PROJECT:
            reg = BundlesRegistry(
                default_bundle_id=get_default_id(),
                bundles={bid: BundleEntry(**info) for bid, info in get_all().items()},
            )
        else:
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
        if not _bundle_allowed_for_session(manifest, session):
            continue
        descriptor: Dict[str, Any] = {
            "id": bid,
            "name": entry.name,
            "description": entry.description,
            "singleton": bool(entry.singleton),
            "version": getattr(entry, "version", None),
        }
        if manifest is not None:
            descriptor.update(_manifest_to_descriptor_filtered(manifest, session))
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
    key = _bundle_props_key(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
    raw = await redis.get(key)
    props = {}
    if raw:
        try:
            props = json.loads(raw)
        except Exception:
            props = {}

    defaults = await _load_bundle_props_defaults(
        bundle_id=bundle_id,
        tenant=tenant_id,
        project=project_id,
        request=request,
        session=session,
    )
    if isinstance(defaults, dict) and "bundle_version" in defaults:
        props = dict(props)
        props["bundle_version"] = defaults.get("bundle_version")

    return {
        "bundle_id": bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "props": props,
        "defaults": defaults,
    }


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

    key = _bundle_props_key(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
    props = dict(payload.props or {})
    props.pop("bundle_version", None)

    if payload.op == "merge":
        raw = await redis.get(key)
        current = {}
        if raw:
            try:
                current = json.loads(raw)
            except Exception:
                current = {}
        props = _deep_merge_props(current, props)
    elif payload.op != "replace":
        raise HTTPException(status_code=400, detail="Invalid op; use 'replace' or 'merge'")

    await redis.set(key, json.dumps(props, ensure_ascii=False))

    try:
        msg = {
            "type": "bundles.props.update",
            "bundle_id": bundle_id,
            "tenant": tenant_id,
            "project": project_id,
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        await redis.publish(
            _bundles_channel(namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL, tenant=tenant_id, project=project_id),
            json.dumps(msg, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Failed to publish props update: %s", e)

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

    defaults = await _load_bundle_props_defaults(
        bundle_id=bundle_id,
        tenant=tenant_id,
        project=project_id,
        request=request,
        session=session,
    )

    redis = _get_app_redis(request)
    key = _bundle_props_key(tenant=tenant_id, project=project_id, bundle_id=bundle_id)
    await redis.set(key, json.dumps(defaults, ensure_ascii=False))

    try:
        msg = {
            "type": "bundles.props.update",
            "bundle_id": bundle_id,
            "tenant": tenant_id,
            "project": project_id,
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        await redis.publish(
            _bundles_channel(namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL, tenant=tenant_id, project=project_id),
            json.dumps(msg, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Failed to publish props reset: %s", e)

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
            await asyncio.to_thread(secrets_manager.set_many, flat)
        else:
            await asyncio.to_thread(secrets_manager.delete_many, keys)
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
            await asyncio.to_thread(
                secrets_manager.set_secret,
                metadata_key,
                json.dumps(sorted(stored_keys), ensure_ascii=False),
            )
        else:
            await asyncio.to_thread(secrets_manager.delete_secret, metadata_key)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store secrets metadata: {exc}") from exc

    try:
        await redis.set(secrets_key, json.dumps(sorted(stored_keys)))
    except Exception:
        pass

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
        raw_keys = get_secret(f"bundles.{bundle_id}.secrets.__keys")
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
            await asyncio.to_thread(secrets_manager.set_many, flat)
        else:
            await asyncio.to_thread(secrets_manager.delete_many, keys)
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
            await asyncio.to_thread(
                secrets_manager.set_secret,
                metadata_key,
                json.dumps(sorted(stored_keys), ensure_ascii=False),
            )
        else:
            await asyncio.to_thread(secrets_manager.delete_secret, metadata_key)
    except SecretsManagerWriteError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to store user secrets metadata: {exc}") from exc

    try:
        await redis.set(secrets_key, json.dumps(sorted(stored_keys)))
    except Exception:
        pass

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
        serialize_to_env,
    )
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches
    from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids
    from kdcube_ai_app.infra.plugin.bundle_store import (
        load_registry as store_load,
        save_registry as store_save,
        apply_update as store_apply,
    )

    redis = _get_app_redis(request)
    try:
        current = await store_load(redis, tenant_id, project_id)
        updated = store_apply(current, payload.op, payload.bundles, payload.default_bundle_id)
        await store_save(redis, updated, tenant_id, project_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        if payload.op == "replace":
            await set_registry_async(payload.bundles, payload.default_bundle_id)
        elif payload.op == "merge":
            await upsert_bundles_async(payload.bundles, payload.default_bundle_id)
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
        reg = get_all()
        default_id = get_default_id()
        serialize_to_env(reg, default_id)
        clear_agentic_caches()
    else:
        reg = {bid: be.model_dump() for bid, be in updated.bundles.items()}
        default_id = updated.default_bundle_id

    try:
        msg = {
            "type": "bundles.update",
            "op": payload.op,
            "bundles": payload.bundles,
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


async def _do_reset_bundles_from_env(
        request: Request,
        session: UserSession,
        payload: Optional[BundleResetEnvRequest] = None,
):
    settings = get_settings()
    from kdcube_ai_app.infra.plugin.bundle_store import reset_registry_from_env
    from kdcube_ai_app.infra.plugin.bundle_registry import set_registry_async, serialize_to_env
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches, evict_bundle_scope, AgenticBundleSpec
    from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids

    tenant_id = (payload.tenant if payload else None) or settings.TENANT
    project_id = (payload.project if payload else None) or settings.PROJECT
    requested_bundle_id = str((payload.bundle_id if payload else "") or "").strip() or None
    redis = _get_app_redis(request)

    try:
        reg = await reset_registry_from_env(redis, tenant_id, project_id)
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

    bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
    eviction_result: dict[str, int] | None = None
    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        await set_registry_async(bundles_dict, reg.default_bundle_id)
        serialize_to_env(bundles_dict, reg.default_bundle_id)
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
                "[bundle.reload] stopped local sidecars during reset-env: tenant=%s project=%s count=%s bundles=%s",
                tenant_id,
                project_id,
                stopped_sidecars,
                sorted(target_bundle_ids),
            )
        if target_entry is not None:
            target_payload = target_entry.model_dump()
            target_spec = AgenticBundleSpec(
                path=target_payload.get("path"),
                module=target_payload.get("module"),
                singleton=bool(target_payload.get("singleton")),
            )
            eviction_result = evict_bundle_scope(target_spec, drop_sys_modules=True)
        else:
            clear_agentic_caches()

    msg = {
        "type": "bundles.update",
        "op": "replace",
        "bundles": bundles_dict,
        "default_bundle_id": reg.default_bundle_id,
        "tenant": tenant_id,
        "project": project_id,
        "updated_by": session.username or session.user_id or "unknown",
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    await redis.publish(
        _bundles_channel(namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL, tenant=tenant_id, project=project_id),
        json.dumps(msg, ensure_ascii=False),
    )

    return {
        "status": "ok",
        "source": "env",
        "default_bundle_id": reg.default_bundle_id,
        "count": len(reg.bundles),
        "bundle_id": requested_bundle_id,
        "eviction": eviction_result,
    }


@admin_router.post("/admin/integrations/bundles/reset-env", status_code=200)
async def admin_reset_bundles_from_env(
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
        payload: Optional[BundleResetEnvRequest] = None,
):
    return await _do_reset_bundles_from_env(request, session, payload)


@internal_router.post("/internal/bundles/reset-env", status_code=200)
async def internal_reset_bundles_from_env(payload: Optional[BundleResetEnvRequest], request: Request):
    """
    Localhost-only descriptor reset for local development / CLI automation.
    Re-applies AGENTIC_BUNDLES_JSON authoritatively and clears bundle caches.
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
    return await _do_reset_bundles_from_env(request, automation_session, payload)


@admin_router.post("/admin/integrations/bundles/cleanup", status_code=200)
async def admin_cleanup_bundles(
        payload: BundleCleanupRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    from kdcube_ai_app.infra.plugin.agentic_loader import evict_inactive_specs, AgenticBundleSpec

    result = {"status": "ok"}

    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        active_specs = []
        for _bid, entry in (get_all() or {}).items():
            try:
                active_specs.append(
                    AgenticBundleSpec(
                        path=entry.get("path"),
                        module=entry.get("module"),
                        singleton=bool(entry.get("singleton")),
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
        redis = _get_app_redis(request)
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
    spec = await resolve_bundle_async(bundle_id, override=None)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' not found")

    # Mirror BaseEntrypoint._apply_configuration_overrides: use dir content hash as
    # authoritative version so the storage path matches what _ensure_ui_build() used.
    try:
        import pathlib as _pathlib
        from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import compute_dir_sha256
        _root = _pathlib.Path(spec.path)
        if spec.module:
            _candidate = _root / spec.module.split(".")[0]
            if _candidate.exists():
                _root = _candidate
        if _root.exists():
            from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import _SKIP_DIRS_DEFAULT
            spec.version = compute_dir_sha256(_root, skip_dirs={*_SKIP_DIRS_DEFAULT, "node_modules"},
                                              skip_files={"package-lock.json"})[:12]
    except Exception:
        pass

    storage_root = storage_for_spec(spec=spec, tenant=tenant_id, project=project_id, ensure=False)
    ui_root = storage_root / "ui" if storage_root else None

    if not ui_root or not ui_root.exists():
        # Build-on-first-request for bundles that expose ui.main_view but were not
        # instantiated yet in this proc. This triggers on_bundle_load(), which in
        # turn calls BaseEntrypoint._ensure_ui_build().
        await _load_bundle_props_defaults(
            bundle_id=bundle_id,
            tenant=tenant_id,
            project=project_id,
            request=request,
            session=session,
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
        base_href = f"/api/integrations/static/{tenant}/{project}/{bundle_id}/"
        content = target.read_text(encoding="utf-8")
        content = content.replace("<head>", f"<head><base href=\"{base_href}\">", 1)
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


@router.post("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}")
async def call_bundle_op_public(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
        session: UserSession = Depends(get_user_session_dependency()),
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
        session=session,
    )


@router.get("/bundles/{tenant}/{project}/{bundle_id}/public/{operation}")
async def call_bundle_op_public_get(
        tenant: str,
        project: str,
        bundle_id: str,
        operation: str,
        request: Request,
        session: UserSession = Depends(get_user_session_dependency()),
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
        session=session,
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
    visible_widgets = _visible_widget_specs(manifest, session)
    visible_apis = _visible_api_specs(manifest, session)
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "ui_widgets": [
            {
                "alias": spec.alias,
                "icon": spec.icon,
                "roles": list(spec.roles),
            }
            for spec in visible_widgets
        ],
        "api_endpoints": [
            {
                "alias": spec.alias,
                "http_method": spec.http_method,
                "route": spec.route,
                "roles": list(spec.roles),
                "public_auth_mode": (spec.public_auth.mode if spec.public_auth else None),
            }
            for spec in visible_apis
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
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "ui_widgets": [
            {
                "alias": spec.alias,
                "icon": spec.icon,
                "roles": list(spec.roles),
            }
            for spec in _visible_widget_specs(manifest, session)
        ],
    }


@router.get("/bundles/{tenant}/{project}/{bundle_id}/widgets/{widget_alias}")
async def fetch_bundle_widget(
        tenant: str,
        project: str,
        bundle_id: str,
        widget_alias: str,
        request: Request,
        session: UserSession = Depends(require_auth(RequireUser())),
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
    widget_spec = resolve_bundle_widget(workflow, alias=widget_alias, bundle_id=spec_resolved.id)
    if widget_spec is None:
        raise HTTPException(status_code=404, detail=f"Bundle does not define widget {widget_alias}")
    if not _roles_visible(widget_spec.roles, session):
        raise HTTPException(status_code=403, detail=f"Bundle widget {widget_alias} is not visible to this user")

    fn = getattr(workflow, widget_spec.method_name)
    extra = _with_implicit_bundle_kwargs(
        _get_query_kwargs(request),
        user_id=session.user_id or session.fingerprint,
        fingerprint=session.fingerprint,
    )
    runtime_comm = _resolve_bound_runtime_comm(workflow=workflow, comm_context=comm_context)
    with bind_current_request_context(comm_context, comm=runtime_comm):
        if inspect.iscoroutinefunction(fn):
            result = await fn(**extra)
        else:
            result = fn(**extra)
    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "widget": {
            "alias": widget_spec.alias,
            "icon": widget_spec.icon,
            "roles": list(widget_spec.roles),
        },
        widget_alias: result,
    }


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
    )


def _resolve_requested_bundle_id(
        *,
        path_bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
) -> str:
    if path_bundle_id and str(path_bundle_id).strip():
        return str(path_bundle_id).strip()

    payload_bundle_id = str(payload.bundle_id or "").strip()
    if payload_bundle_id:
        return payload_bundle_id

    cfg_bundle_id = str(getattr(payload.config_request, "agentic_bundle_id", "") or "").strip()
    if cfg_bundle_id:
        return cfg_bundle_id

    default_bundle_id = str(get_default_id() or "").strip()
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


def _resolve_bundle_secret_key(*, bundle_id: str, secret_key: str) -> str:
    key = str(secret_key or "").strip()
    if not key:
        raise ValueError("Bundle public endpoint secret key is empty")
    if key.startswith("bundles."):
        return key
    return f"bundles.{bundle_id}.secrets.{key}"


def _enforce_public_api_auth(
        *,
        endpoint_spec: APIEndpointSpec,
        bundle_id: str,
        operation: str,
        request: Request,
) -> None:
    if endpoint_spec.route != "public":
        return

    public_auth = endpoint_spec.public_auth
    if public_auth is None:
        raise HTTPException(
            status_code=403,
            detail=f"Bundle public operation {operation} is not configured for public auth",
        )

    if public_auth.mode == "none":
        return

    if public_auth.mode != "header_secret":
        logger.error(
            "Unsupported public auth mode for bundle %s operation %s: %s",
            bundle_id,
            operation,
            public_auth.mode,
        )
        raise HTTPException(status_code=500, detail="Unsupported public auth mode")

    header_name = str(public_auth.header or "").strip()
    provided_secret = str(request.headers.get(header_name) or "")
    if not provided_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    resolved_secret_key = _resolve_bundle_secret_key(
        bundle_id=bundle_id,
        secret_key=str(public_auth.secret_key or ""),
    )
    expected_secret = get_secret(resolved_secret_key)
    if not expected_secret:
        logger.warning(
            "Bundle public operation %s requires missing secret %s",
            operation,
            resolved_secret_key,
        )
        raise HTTPException(status_code=503, detail="Public endpoint is not configured")
    if not hmac.compare_digest(provided_secret, expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _load_bundle_workflow(
        *,
        tenant: str,
        project: str,
        bundle_id: Optional[str],
        payload: BundleSuggestionsRequest,
        request: Request,
        session: UserSession,
) -> tuple[Any, Any, str, str, ChatTaskPayload]:
    settings = get_settings()
    cfg_req = payload.config_request or ConfigRequest()
    tenant_id, project_id = _resolve_path_scope(tenant=tenant, project=project)
    _bind_route_scope_to_config_request(cfg_req, tenant=tenant_id, project=project_id)

    if not cfg_req.selected_model:
        cfg_req.selected_model = (namespaces.CONFIG.AGENTIC.DEFAULT_LLM_MODEL_CONFIG or {}).get("model_name",
                                                                                                "gpt-4o-mini")
    if not cfg_req.selected_embedder:
        cfg_req.selected_embedder = (namespaces.CONFIG.AGENTIC.DEFAULT_EMBEDDING_MODEL_CONFIG or {}).get("model_name",
                                                                                                         "gpt-4o-mini")
    if not cfg_req.openai_api_key:
        cfg_req.openai_api_key = settings.OPENAI_API_KEY
    if not cfg_req.claude_api_key:
        cfg_req.claude_api_key = settings.ANTHROPIC_API_KEY

    requested_bundle_id = _resolve_requested_bundle_id(path_bundle_id=bundle_id, payload=payload)
    cfg_req.agentic_bundle_id = requested_bundle_id
    request_id = str(uuid.uuid4())

    spec_resolved = await resolve_bundle_async(cfg_req.agentic_bundle_id, override=None)
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {cfg_req.agentic_bundle_id} not found")

    wf_config = create_workflow_config(cfg_req)
    wf_config.ai_bundle_spec = spec_resolved

    spec = AgenticBundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    routing = _build_rest_bundle_routing(
        request=request,
        session_id=session.session_id,
        bundle_id=spec_resolved.id,
    )
    comm_context = ChatTaskPayload(
        request=ChatTaskRequest(request_id=request_id),
        routing=routing,
        actor=ChatTaskActor(
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        user=ChatTaskUser(
            user_type=session.user_type.value,
            user_id=session.user_id,
            username=session.username,
            fingerprint=session.fingerprint,
            roles=session.roles,
            permissions=session.permissions,
            timezone=session.request_context.user_timezone,
            utc_offset_min=session.request_context.user_utc_offset_min,
        ),
    )

    redis = _get_app_redis(request)
    try:
        workflow, _mod = get_workflow_instance(
            spec, wf_config, comm_context=comm_context, redis=redis,
        )
    except Exception as e:
        logger.exception(f"[call_bundle_op.{tenant}.{project}] Failed to load bundle {asdict(spec)}")
        try:
            admin_spec = await resolve_bundle_async("kdcube.admin", override=None)
            if not admin_spec:
                raise e
            wf_config.ai_bundle_spec = admin_spec
            admin = AgenticBundleSpec(
                path=admin_spec.path,
                module=admin_spec.module,
                singleton=bool(admin_spec.singleton),
            )
            workflow, _mod = get_workflow_instance(
                admin, wf_config, comm_context=comm_context, redis=redis,
            )
            spec_resolved = admin_spec
        except Exception:
            raise HTTPException(status_code=500, detail=f"Failed to load bundle: {e}")

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

    request_method = str(getattr(request, "method", "POST") or "POST").upper()
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
    _enforce_public_api_auth(
        endpoint_spec=endpoint_spec,
        bundle_id=spec_resolved.id,
        operation=operation,
        request=request,
    )
    if not _roles_visible(endpoint_spec.roles, session):
        raise HTTPException(status_code=403, detail=f"Bundle operation {operation} is not visible to this user")

    try:
        fn = getattr(workflow, endpoint_spec.method_name)
        extra = payload.data or {}
        if request_method == "GET":
            extra = _get_query_kwargs(request)
        elif uploaded_files:
            extra = dict(extra or {})
            extra["uploaded_files"] = uploaded_files
        extra = _with_implicit_bundle_kwargs(
            extra,
            user_id=session.user_id or session.fingerprint,
            fingerprint=session.fingerprint,
        )
        runtime_comm = _resolve_bound_runtime_comm(workflow=workflow, comm_context=comm_context)
        with bind_current_request_context(comm_context, comm=runtime_comm):
            if inspect.iscoroutinefunction(fn):
                result = await fn(**extra)
            else:
                result = fn(**extra)
    except Exception as e:
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
