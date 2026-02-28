# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/proc/rest/integrations/integrations.py
import asyncio
import inspect
import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from pydantic import BaseModel

from kdcube_ai_app.apps.chat.api.resolvers import require_auth, auth_without_pressure
from kdcube_ai_app.auth.AuthManager import RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ChatTaskPayload,
    ChatTaskRouting,
    ChatTaskActor,
    ChatTaskUser,
    ChatTaskRequest,
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
from kdcube_ai_app.infra.plugin.agentic_loader import AgenticBundleSpec, get_workflow_instance
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
        raise RuntimeError("redis_async is not initialized on app.state")
    return redis


router = APIRouter()
admin_router = APIRouter()


class BundleSuggestionsRequest(BaseModel):
    bundle_id: Optional[str] = None
    conversation_id: Optional[str] = None
    config_request: Optional[ConfigRequest] = None


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


class BundleCleanupRequest(BaseModel):
    drop_sys_modules: bool = True
    tenant: Optional[str] = None
    project: Optional[str] = None


class BundleResetEnvRequest(BaseModel):
    tenant: Optional[str] = None
    project: Optional[str] = None


def _bundles_channel(fmt: str, *, tenant: str, project: str) -> str:
    return fmt.format(tenant=tenant, project=project)


def _bundle_props_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.PROPS_KEY_FMT.format(
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
) -> Dict[str, Any]:
    from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle_async

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
    routing = ChatTaskRouting(
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
        if getattr(spec_resolved, "git_ref", None):
            defaults["git_ref"] = str(spec_resolved.git_ref)
        if getattr(spec_resolved, "git_url", None):
            defaults["git_url"] = str(spec_resolved.git_url)
    except Exception:
        pass
    return defaults


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

    return {
        "tenant": tenant_id,
        "project": project_id,
        "available_bundles": {
            bid: {
                "id": bid,
                "name": entry.name,
                "description": entry.description,
                "path": entry.path,
                "module": entry.module,
                "singleton": bool(entry.singleton),
                "version": getattr(entry, "version", None),
                "git_url": getattr(entry, "git_url", None),
                "git_ref": getattr(entry, "git_ref", None),
                "git_subdir": getattr(entry, "git_subdir", None),
                "git_commit": getattr(entry, "git_commit", None),
            }
            for bid, entry in reg.bundles.items()
        },
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
        current.update(props)
        props = current
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


@admin_router.post("/admin/integrations/bundles", status_code=200)
async def admin_set_bundles(
    payload: AdminBundlesUpdateRequest,
    request: Request,
    session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    from kdcube_ai_app.infra.plugin.bundle_registry import (
        set_registry_async,
        upsert_bundles_async,
        serialize_to_env,
    )
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches
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


@admin_router.post("/admin/integrations/bundles/reset-env", status_code=200)
async def admin_reset_bundles_from_env(
    request: Request,
    session: UserSession = Depends(auth_without_pressure()),
    payload: Optional[BundleResetEnvRequest] = None,
):
    settings = get_settings()
    from kdcube_ai_app.infra.plugin.bundle_store import reset_registry_from_env
    from kdcube_ai_app.infra.plugin.bundle_registry import set_registry_async, serialize_to_env
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches

    tenant_id = (payload.tenant if payload else None) or settings.TENANT
    project_id = (payload.project if payload else None) or settings.PROJECT
    redis = _get_app_redis(request)

    try:
        reg = await reset_registry_from_env(redis, tenant_id, project_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
    if tenant_id == settings.TENANT and project_id == settings.PROJECT:
        await set_registry_async(bundles_dict, reg.default_bundle_id)
        serialize_to_env(bundles_dict, reg.default_bundle_id)
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
    }


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


@router.post("/bundles/{tenant}/{project}/operations/{operation}")
async def call_bundle_op(
    tenant: str,
    project: str,
    request: Request,
    payload: BundleSuggestionsRequest = Body(default_factory=BundleSuggestionsRequest),
    operation: str = "suggestions",  # news, etc.
    session: UserSession = Depends(require_auth(RequireUser())),
):
    """
    Load (or reuse singleton) bundle instance and call its operation (e.g. suggestions()).
    Returns generic JSON from the bundle.
    """
    settings = get_settings()
    sem = _get_integrations_semaphore()
    if sem:
        async with sem:
            return await _call_bundle_op_inner(
                tenant=tenant,
                project=project,
                payload=payload,
                request=request,
                operation=operation,
                session=session,
            )
    return await _call_bundle_op_inner(
        tenant=tenant,
        project=project,
        payload=payload,
        request=request,
        operation=operation,
        session=session,
    )


async def _call_bundle_op_inner(
    *,
    tenant: str,
    project: str,
    payload: BundleSuggestionsRequest,
    request: Request,
    operation: str,
    session: UserSession,
):
    settings = get_settings()
    cfg_req = payload.config_request or ConfigRequest()

    # Ensure model defaults exist
    if not cfg_req.selected_model:
        cfg_req.selected_model = (namespaces.CONFIG.AGENTIC.DEFAULT_LLM_MODEL_CONFIG or {}).get("model_name", "gpt-4o-mini")
    if not cfg_req.selected_embedder:
        cfg_req.selected_embedder = (namespaces.CONFIG.AGENTIC.DEFAULT_EMBEDDING_MODEL_CONFIG or {}).get("model_name", "gpt-4o-mini")
    if not cfg_req.openai_api_key:
        cfg_req.openai_api_key = os.getenv("OPENAI_API_KEY")
    if not cfg_req.claude_api_key:
        cfg_req.claude_api_key = os.getenv("ANTHROPIC_API_KEY")

    if payload.bundle_id:
        cfg_req.agentic_bundle_id = payload.bundle_id

    tenant_id = cfg_req.tenant or tenant or settings.TENANT
    project_id = cfg_req.project or project or settings.PROJECT
    request_id = str(uuid.uuid4())

    # 1) Resolve bundle from registry
    spec_resolved = await resolve_bundle_async(cfg_req.agentic_bundle_id, override=None)
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {cfg_req.agentic_bundle_id} not found")

    # 2) Build workflow config
    wf_config = create_workflow_config(cfg_req)
    wf_config.ai_bundle_spec = spec_resolved

    spec = AgenticBundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    routing = ChatTaskRouting(
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

    # 4) Call op() if available (support sync/async)
    if not hasattr(workflow, operation) or not callable(getattr(workflow, operation)):
        raise HTTPException(status_code=404, detail=f"Bundle does not support operation {operation}")

    try:
        user_id = session.user_id or session.fingerprint
        fn = getattr(workflow, operation)
        if inspect.iscoroutinefunction(fn):
            result = await fn(user_id=user_id, fingerprint=session.fingerprint)
        else:
            result = fn(user_id=user_id, fingerprint=session.fingerprint)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{operation}() failed: {e}")

    return {
        "status": "ok",
        "tenant": tenant_id,
        "project": project_id,
        "bundle_id": spec_resolved.id,
        "conversation_id": payload.conversation_id,
        operation: result,
    }
