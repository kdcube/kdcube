# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

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
from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest
from kdcube_ai_app.apps.chat.sdk.protocol import ServiceCtx, ConversationCtx, ChatTaskUser
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
                "ORCHESTRATOR_IDENTITY",
                f"kdcube_orchestrator_{os.environ.get('ORCHESTRATOR_TYPE', 'dramatiq')}",
            ),
        )

    app.state.chat_comm = comm
    return comm

@router.get("/landing/bundles")
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
            }
            for bid, entry in reg.bundles.items()
        },
        "default_bundle_id": reg.default_bundle_id
    }

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

@router.post("/integrations/bundles/{tenant}/{project}/operations/{operation}")
async def get_bundle_suggestions(
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

    # 1) Resolve bundle from the in-process registry (keeps processor-owned semantics)
    spec_resolved = resolve_bundle(config_request.agentic_bundle_id, override=None)
    # 2) Build minimal workflow config (project-aware; defaults elsewhere)
    try:
        wf_config = create_workflow_config(ConfigRequest())
    except Exception:
        # If ConfigRequest signature changes, be defensive
        wf_config = create_workflow_config(ConfigRequest.model_validate({"project": project}))

    chat_comm = _ensure_chat_communicator(request.app)
    user = ChatTaskUser(
        user_type=session.user_type.value,
        user_id=session.user_id,
        username=session.username,
        fingerprint=session.fingerprint,
        roles=session.roles,
        permissions=session.permissions,
    )
    svc_ctx = ServiceCtx(
        request_id=str(uuid4()),
        tenant=tenant,
        project=project,
        user=session.user_id or session.fingerprint,
        user_obj=user
    )
    conv_ctx = ConversationCtx(
        session_id=session.session_id,
        conversation_id=payload.conversation_id or session.session_id,
        turn_id=f"turn_{uuid4().hex[:8]}",
    )

    # Bind to this session/thread; no socket_id in REST call (target_sid=None)
    bound_comm = chat_comm.bind(
        service=svc_ctx.model_dump(),
        conversation=conv_ctx.model_dump(),
        session_id=session.session_id,
        target_sid=None,
    )

    # --- Instantiate workflow with the bound communicator (new-style) ---
    spec = AgenticBundleSpec(
        path=spec_resolved.path,
        module=spec_resolved.module,
        singleton=bool(spec_resolved.singleton),
    )
    try:
        # I need to create communicator here:
        wf_config.ai_bundle_spec = spec_resolved
        communicator = None #
        workflow, _init_state, _mod = get_workflow_instance(
            spec, wf_config, communicator=bound_comm,
        )
    except Exception as e:
        logger.exception(f"[get_bundle_suggestions.{tenant}.{project}] Failed to load bundle {asdict(spec)}")
        raise HTTPException(status_code=500, detail=f"Failed to load bundle: {e}")

    # 4) Call op() if available (support sync/async)
    if not hasattr(workflow, operation) or not callable(getattr(workflow, operation)):
        # Graceful, generic reply if not implemented
        # return {
        #     "status": "ok",
        #     "tenant": tenant,
        #     "project": project,
        #     "bundle_id": spec_resolved.id,
        #     "conversation_id": payload.conversation_id,
        #     operation: None,
        #     "error": f"bundle does not support operation {operation}"
        # }
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
