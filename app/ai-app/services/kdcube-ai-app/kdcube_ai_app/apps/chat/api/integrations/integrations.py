# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/api/integrations/integrations.py
import uuid
from dataclasses import asdict
from typing import Optional, Dict, Any
import logging
import json
import os
import inspect

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel
from fastapi import Depends, HTTPException, Request, APIRouter

from kdcube_ai_app.apps.chat.api.resolvers import get_user_session_dependency, auth_without_pressure, REDIS_URL
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest
from kdcube_ai_app.apps.chat.sdk.protocol import ServiceCtx, ConversationCtx, ChatTaskUser, ChatTaskPayload, \
    ChatTaskActor, ChatTaskRequest, ChatTaskRouting
from kdcube_ai_app.auth.sessions import UserSession

import kdcube_ai_app.infra.namespaces as namespaces
from kdcube_ai_app.infra.plugin.bundle_store import (
    load_registry, BundlesRegistry, BundleEntry
)
from kdcube_ai_app.infra.plugin.bundle_registry import (
    get_all, get_default_id
)

"""
Integrations API

File: api/integrations/integrations.py
"""


logger = logging.getLogger("KBMonitoring.API")

# Create router
router = APIRouter()

class AdminBundlesUpdateRequest(BaseModel):
    op: str = "merge"  # "replace" | "merge"
    bundles: Dict[str, Dict[str, Any]]
    default_bundle_id: Optional[str] = None

class BundleSuggestionsRequest(BaseModel):
    bundle_id: Optional[str] = None
    conversation_id: Optional[str] = None
    config_request: Optional[ConfigRequest] = None

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

def _ensure_chat_communicator(app) -> ChatRelayCommunicator:
    """
    Return a process-wide ChatRelayCommunicator.
    Reuse app.state.chat_comm if present, otherwise create one.
    Prefer the Socket.IO handler's ServiceCommunicator to avoid extra Redis clients.
    """
    comm = getattr(app.state, "chat_comm", None)
    if comm:
        return comm

    # Try reuse the Socket.IO chat handler's ServiceCommunicator (if available)
    svc_comm = None
    try:
        sio_handler = getattr(app.state, "socketio_handler", None)
        svc_comm = getattr(sio_handler, "_comm", None)  # ServiceCommunicator
    except Exception:
        svc_comm = None

    if svc_comm:
        comm = ChatRelayCommunicator(comm=svc_comm)
    else:
        # Fall back to a fresh one (same identity/env as processor/web)
        comm = ChatRelayCommunicator(
            redis_url=REDIS_URL,
            orchestrator_identity=os.environ.get(
                "CB_RELAY_IDENTITY",
                f"kdcube.relay.{os.environ.get('CB_ORCHESTRATOR_TYPE', 'dramatiq')}" or "kdcube.relay.chatbot",
            ),
        )

    app.state.chat_comm = comm
    return comm

@router.get("/admin/integrations/bundles")
async def get_available_bundles(
        request: Request,
        session: UserSession = Depends(get_user_session_dependency())
):
    """
    Returns configured bundles for selection in the UI.
    Read from Redis (source of truth), fallback to in-memory if needed.
    """
    try:
        redis = request.app.state.middleware.redis  # set in web_app during startup
        reg = await load_registry(redis)
    except Exception:
        # fall back to in-memory (should be rare)
        reg = BundlesRegistry(
            default_bundle_id=get_default_id(),
            bundles={bid: BundleEntry(**info) for bid, info in get_all().items()}
        )

    return {
            "available_bundles": {
            bid: {
                "id": bid,
                "name": entry.name,
                "description": entry.description,
                "path": entry.path,
                "module": entry.module,
                "singleton": bool(entry.singleton),
                "version": getattr(entry, "version", None),
            }
            for bid, entry in reg.bundles.items()
        },
        "default_bundle_id": reg.default_bundle_id
    }

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
    from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config
    from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
    from kdcube_ai_app.infra.plugin.agentic_loader import AgenticBundleSpec, get_workflow_instance

    spec_resolved = resolve_bundle(bundle_id, override=None)
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
    workflow, _mod = get_workflow_instance(
        spec, wf_config, comm_context=comm_context,
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
    return defaults

@router.get("/admin/integrations/bundles/{bundle_id}/props")
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

    redis = request.app.state.middleware.redis
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

@router.post("/admin/integrations/bundles/{bundle_id}/props", status_code=200)
async def set_bundle_props(
        bundle_id: str,
        payload: BundlePropsUpdateRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure()),
):
    settings = get_settings()
    tenant_id = payload.tenant or settings.TENANT
    project_id = payload.project or settings.PROJECT
    redis = request.app.state.middleware.redis

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
        await redis.publish(namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL, json.dumps(msg, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to publish props update: {e}")

    return {"status": "ok", "bundle_id": bundle_id, "tenant": tenant_id, "project": project_id}

@router.post("/admin/integrations/bundles/{bundle_id}/props/reset-code", status_code=200)
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

    redis = request.app.state.middleware.redis
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
        await redis.publish(namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL, json.dumps(msg, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to publish props reset: {e}")

    return {"status": "ok", "bundle_id": bundle_id, "tenant": tenant_id, "project": project_id, "source": "code"}

@router.post("/admin/integrations/bundles", status_code=200)
async def admin_set_bundles(
        payload: AdminBundlesUpdateRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    from kdcube_ai_app.infra.plugin.bundle_registry import (
        set_registry, upsert_bundles, serialize_to_env, get_all, get_default_id
    )
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches

    if payload.op == "replace":
        set_registry(payload.bundles, payload.default_bundle_id)
    elif payload.op == "merge":
        upsert_bundles(payload.bundles, payload.default_bundle_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid op; use 'replace' or 'merge'")

    reg = get_all()
    default_id = get_default_id()
    serialize_to_env(reg, default_id)
    clear_agentic_caches()

    # Publish to all nodes
    try:
        msg = {
            "type": "bundles.update",
            "op": payload.op,
            "bundles": payload.bundles,
            "default_bundle_id": payload.default_bundle_id,
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z"
        }
        redis = request.app.state.middleware.redis
        await redis.publish(namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL, json.dumps(msg, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to publish config update: {e}")

    return {"status": "ok", "default_bundle_id": default_id, "count": len(reg)}

@router.post("/admin/integrations/bundles/reset-env", status_code=200)
async def admin_reset_bundles_from_env(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    from kdcube_ai_app.infra.plugin.bundle_store import reset_registry_from_env
    from kdcube_ai_app.infra.plugin.bundle_registry import set_registry, serialize_to_env
    from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches

    redis = request.app.state.middleware.redis

    try:
        # Force overwrite Redis from env
        reg = await reset_registry_from_env(redis)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Mirror to in-memory registry and env for consistency
    bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
    set_registry(bundles_dict, reg.default_bundle_id)
    serialize_to_env(bundles_dict, reg.default_bundle_id)
    clear_agentic_caches()

    # Broadcast to all servers
    msg = {
        "type": "bundles.update",
        "op": "replace",
        "bundles": bundles_dict,
        "default_bundle_id": reg.default_bundle_id,
        "updated_by": session.username or session.user_id or "unknown",
        "ts": datetime.utcnow().isoformat() + "Z"
    }
    await redis.publish(namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL, json.dumps(msg, ensure_ascii=False))

    return {
        "status": "ok",
        "source": "env",
        "default_bundle_id": reg.default_bundle_id,
        "count": len(reg.bundles)
    }

@router.post("/admin/integrations/bundles/cleanup", status_code=200)
async def admin_cleanup_bundles(
        payload: BundleCleanupRequest,
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    from kdcube_ai_app.infra.plugin.agentic_loader import evict_inactive_specs, AgenticBundleSpec
    from kdcube_ai_app.infra.plugin.bundle_registry import get_all

    active_specs = []
    for _bid, entry in (get_all() or {}).items():
        try:
            active_specs.append(AgenticBundleSpec(
                path=entry.get("path"),
                module=entry.get("module"),
                singleton=bool(entry.get("singleton")),
            ))
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
            "updated_by": session.username or session.user_id or "unknown",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        redis = request.app.state.middleware.redis
        await redis.publish(namespaces.CONFIG.BUNDLES.CLEANUP_CHANNEL, json.dumps(msg, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Failed to publish bundles cleanup: {e}")

    return result

@router.post("/integrations/bundles/{tenant}/{project}/operations/{operation}")
async def call_bundle_op(
        tenant: str,
        project: str,
        payload: BundleSuggestionsRequest,
        request: Request,
        operation: str = "suggestions", # news, etc.
        session: UserSession = Depends(get_user_session_dependency()),
):
    """
    Load (or reuse singleton) bundle instance and, if defined, call its `suggestions(...)`.
    Returns generic JSON from the bundle, or an empty suggestions list when not implemented.
    """
    from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config
    from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
    from kdcube_ai_app.infra.plugin.agentic_loader import AgenticBundleSpec, get_workflow_instance

    config_data = {}
    settings = get_settings()

    config_request = ConfigRequest(**config_data)
    if not config_request.selected_model:
        config_request.selected_model = (namespaces.CONFIG.AGENTIC.DEFAULT_LLM_MODEL_CONFIG or {}).get("model_name", "gpt-4o-mini")
    if not config_request.selected_model:
        config_request.selected_embedder = (namespaces.CONFIG.AGENTIC.DEFAULT_EMBEDDING_MODEL_CONFIG or {}).get("model_name", "gpt-4o-mini")
    if not config_request.openai_api_key:
        config_request.openai_api_key = os.getenv("OPENAI_API_KEY")
    if not config_request.claude_api_key:
        config_request.claude_api_key = os.getenv("ANTHROPIC_API_KEY")
    if payload and payload.bundle_id:
        config_request.agentic_bundle_id = payload.bundle_id

    tenant_id = payload.config_request.tenant if payload and payload.config_request and payload.config_request.tenant else settings.TENANT
    project_id = payload.config_request.project if payload and payload.config_request and payload.config_request.project else settings.PROJECT
    request_id = str(uuid.uuid4())

    # 1) Resolve bundle from the in-process registry (keeps processor-owned semantics)
    spec_resolved = resolve_bundle(config_request.agentic_bundle_id, override=None)
    if not spec_resolved:
        raise HTTPException(status_code=404, detail=f"Bundle {config_request.agentic_bundle_id} not found")

    # 2) Build minimal workflow config (project-aware; defaults elsewhere)
    try:
        wf_config = create_workflow_config(ConfigRequest())
    except Exception:
        # If ConfigRequest signature changes, be defensive
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
    try:
        wf_config.ai_bundle_spec = spec_resolved
        workflow, _mod = get_workflow_instance(
            spec, wf_config, comm_context=comm_context,
        )
    except Exception as e:
        logger.exception(f"[get_bundle_suggestions.{tenant}.{project}] Failed to load bundle {asdict(spec)}")
        try:
            admin_spec = resolve_bundle("kdcube.admin", override=None)
            if not admin_spec:
                raise e
            wf_config.ai_bundle_spec = admin_spec
            admin = AgenticBundleSpec(
                path=admin_spec.path,
                module=admin_spec.module,
                singleton=bool(admin_spec.singleton),
            )
            workflow, _mod = get_workflow_instance(
                admin, wf_config, comm_context=comm_context,
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
            result = await fn(user_id=user_id,
                              fingerprint=session.fingerprint)
        else:
            result = fn(user_id=user_id,
                        fingerprint=session.fingerprint)
    except Exception as e:
        # Let bundles raise and still keep a predictable envelope here
        raise HTTPException(status_code=500, detail=f"{operation}() failed: {e}")

    # 5) Envelope the bundle’s generic JSON
    return {
        "status": "ok",
        "tenant": tenant,
        "project": project,
        "bundle_id": spec_resolved.id,
        "conversation_id": payload.conversation_id,
        operation: result,  # arbitrary JSON from bundle
    }
