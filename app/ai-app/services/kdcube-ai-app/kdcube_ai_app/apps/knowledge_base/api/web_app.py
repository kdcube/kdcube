# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
import json
import os, traceback

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv, find_dotenv

from kdcube_ai_app.apps.utils.cors import configure_cors

load_dotenv(find_dotenv())

from kdcube_ai_app.apps.knowledge_base.api.resolvers import (get_project, kb_workdir,
                                                             get_orchestrator, ORCHESTRATOR_TYPE,
                                                             ORCHESTRATOR_IDENTITY, embedding_model, get_tenant,
                                                             REDIS_URL, get_heartbeats_mgr_and_middleware, INSTANCE_ID,
                                                             KB_PORT, HEARTBEAT_INTERVAL, get_idp,
                                                             get_kb_write_with_acct_dep, KDCUBE_STORAGE_PATH,
                                                             get_kb_auth_with_accounting, DEFAULT_PROJECT, kbs,
                                                             get_kb_for_project)
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session

from kdcube_ai_app.infra.orchestration.orchestration import IOrchestrator

from kdcube_ai_app.apps.knowledge_base.api.socketio.kb import SocketIOKBHandler

KDCUBE_ORCHESTRATOR_QUEUES_PREFIX = "kdcube_orch_"
task_queues = []
tasks = {
    "KB_PROCESS": {
        "task_name": "process_kb_resource",
        "queue_name": f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}low_priority"
    }
}

import kdcube_ai_app.apps.utils.logging_config as logging_config
logging_config.configure_logging()
logger = logging.getLogger("KB.WebApp")

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await middleware.init_redis()
    await heartbeat_manager.start_heartbeat(interval=HEARTBEAT_INTERVAL)
    logger.info(f"KB service heartbeat started on instance {INSTANCE_ID}")
    # --- Socket.IO: modular handler ---
    try:
        kb_sio_handler = SocketIOKBHandler(
            allowed_origins=allowed_origins,
            redis_url=REDIS_URL,
            orchestrator_identity=ORCHESTRATOR_IDENTITY,
            instance_id=INSTANCE_ID,
            auth_with_acct=get_kb_auth_with_accounting(),  # << one object
            component_name="kb-socket",
        )

        socket_asgi = kb_sio_handler.get_asgi_app()
        if socket_asgi:
            app.mount("/socket.io", socket_asgi)
            app.state.socketio_handler = kb_sio_handler
            await kb_sio_handler.start()
            logger.info(f"Socket.IO KB handler mounted and listener started")
        else:
            app.state.socketio_handler = None
            logger.warning("Socket.IO not available")
    except Exception as e:
        logger.error(f"Failed to setup Socket.IO KB handler: {e}")
        app.state.socketio_handler = None

    logger.info(f"Orchestrator listener started for {ORCHESTRATOR_TYPE}")

    yield
    # shutdown
    if getattr(app.state, "socketio_handler", None):
        try:
            await app.state.socketio_handler.stop()
        except Exception as e:
            logger.warning(f"Error stopping Socket.IO handler: {e}")
    await heartbeat_manager.stop_heartbeat()
    logger.info(f"KB service heartbeat stopped on instance {INSTANCE_ID}")

    logger.info("Background tasks shut down")


app = FastAPI(title="KB API", description="KB API", lifespan=lifespan)

configure_cors(app)

# ================================================================================
#                            KB INITIALIZATION
# ================================================================================

# ================================================================================
#                      ORCHESTRATOR INITIALIZATION
# =========================================================orchestrator=======================


# .env

# Create orchestrator instance
orchestrator: IOrchestrator = get_orchestrator()

middleware, heartbeat_manager = get_heartbeats_mgr_and_middleware()

logger.info(f"Initialized {ORCHESTRATOR_TYPE} orchestrator with identity: {ORCHESTRATOR_IDENTITY}")

kb_search_load = 0


@app.middleware("http")
async def track_kb_search_load(request, call_next):
    global kb_search_load
    is_search_request = "/search" in str(request.url)

    if is_search_request:
        kb_search_load += 1
        heartbeat_manager.set_load(kb_search_load)
        logger.debug(f"KB search load increased to {kb_search_load}")

    try:
        response = await call_next(request)
        return response
    finally:
        if is_search_request:
            kb_search_load = max(0, kb_search_load - 1)
            heartbeat_manager.set_load(kb_search_load)
            logger.debug(f"KB search load decreased to {kb_search_load}")


# ---------------------------------------
# KB Processing Endpoints
# ---------------------------------------
@app.post("/api/kb/upload/process")
@app.post("/api/kb/{project}/upload/process")
async def process_kb_file_with_socket(request: Request,
                                      project: str = Depends(get_project),
                                      session: UserSession = Depends(get_kb_write_with_acct_dep())):
    data = await request.json()
    if not data or "resource_metadata" not in data or "socket_id" not in data:
        raise HTTPException(status_code=400, detail="Missing resource_metadata or socket_id")

    resource = data["resource_metadata"]
    target_sid = data["socket_id"]
    resource_id = resource["id"]
    version = resource["version"]
    tenant_id = get_tenant()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    processing_mode = data.get("processing_mode", "retrieval_only")

    acct = build_envelope_from_session(
        session=session,
        tenant_id=tenant_id,
        project_id=project,
        request_id=req_id,
        component="kb.rest",  # or "chat.socket", etc.
        metadata={"entrypoint": "/api/kb/upload/process"},
        seeds=[],  # optional SystemResource list
    ).to_dict()

    handler = getattr(app.state, "socketio_handler", None)
    if not handler or not handler.sio:
        raise HTTPException(status_code=500, detail="Socket.IO not available")

    try:
        logger.info(f"BEFORE submitting KB processing task to {ORCHESTRATOR_TYPE} for resource {resource_id}")

        # CLIENT SIDE: Get proper task and queue names for current orchestrator
        task_info = tasks.get("KB_PROCESS")
        session_dict = session.serialize_to_dict()
        result = orchestrator.submit_task(
            task_name=task_info["task_name"],  # Proper name for current orchestrator
            queue=task_info["queue_name"],  # Proper queue for current orchestrator
            kdcube_path=KDCUBE_STORAGE_PATH,
            resource_id=resource_id,
            version=version,
            target_sid=target_sid,
            processing_mode=processing_mode,
            ctx=acct,
            stages_config={
                # "metadata": {
                #     "model_record": metadata_model().model_dump(),
                #     "use_batch": False
                # },
                "embedding": {
                    "model_record": embedding_model().model_dump()
                },
                "search_indexing": {
                    "enabled": True
                }
            }
        )

        if result.status == 'failed':
            raise HTTPException(status_code=500, detail=f"Task submission failed: {result.error}")

        logger.info(f"AFTER submitting to {ORCHESTRATOR_TYPE}, task_id: {result.task_id}")

        return {
            "status": "processing_started",
            "orchestrator_type": ORCHESTRATOR_TYPE,
            "resource_id": resource_id,
            "task_id": result.task_id,
            "message": f"KB processing started with {ORCHESTRATOR_TYPE}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to dispatch KB processing task: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/kb/add-url/process")
@app.post("/api/kb/{project}/add-url/process")
async def process_kb_url_with_socket(request: Request,
                                     project: str = Depends(get_project),
                                     session: UserSession = Depends(get_kb_write_with_acct_dep())):
    """
    Start processing for an already-added URL resource.
    Body must include:
      - resource_metadata: { id, version, ... }
      - socket_id: string
      - processing_mode?: string (optional, defaults to 'retrieval_only')
    """
    data = await request.json()
    if not data or "resource_metadata" not in data or "socket_id" not in data:
        raise HTTPException(status_code=400, detail="Missing resource_metadata or socket_id")

    handler = getattr(app.state, "socketio_handler", None)
    if not handler or not handler.sio:
        raise HTTPException(status_code=500, detail="Socket.IO not available")

    resource = data["resource_metadata"]
    target_sid = data["socket_id"]
    resource_id = resource.get("id")
    version = resource.get("version")
    if not resource_id or version is None:
        raise HTTPException(status_code=400, detail="resource_metadata.id and .version are required")
    tenant_id = get_tenant()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    processing_mode = data.get("processing_mode", "retrieval_only")
    acct = build_envelope_from_session(
        session=session,
        tenant_id=tenant_id,
        project_id=project,
        request_id=req_id,
        component="kb.rest",  # or "chat.socket", etc.
        metadata={"entrypoint": "/api/kb/upload/process"},
        seeds=[],  # optional SystemResource list
    ).to_dict()

    try:
        logger.info(f"BEFORE submitting KB processing task to {ORCHESTRATOR_TYPE} for resource {resource_id}")

        task_info = tasks.get("KB_PROCESS")
        # session_dict = session.serialize_to_dict()

        result = orchestrator.submit_task(
            task_name=task_info["task_name"],
            queue=task_info["queue_name"],
            kdcube_path=KDCUBE_STORAGE_PATH,
            resource_id=resource_id,
            version=version,
            target_sid=target_sid,
            processing_mode=processing_mode,
            ctx=acct,
            stages_config={
                # "metadata": {
                #     "model_record": metadata_model().model_dump(),
                #     "use_batch": False
                # },
                "embedding": {
                    "model_record": embedding_model().model_dump()
                },
                "search_indexing": {
                    "enabled": True
                }
            }
        )

        if result.status == 'failed':
            raise HTTPException(status_code=500, detail=f"Task submission failed: {result.error}")

        logger.info(f"AFTER submitting to {ORCHESTRATOR_TYPE}, task_id: {result.task_id}")

        return {
            "status": "processing_started",
            "orchestrator_type": ORCHESTRATOR_TYPE,
            "resource_id": resource_id,
            "resource_metadata": resource,
            "task_id": result.task_id,
            "message": f"KB processing started with {ORCHESTRATOR_TYPE}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to dispatch URL processing task: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/kb/health")
async def kb_health_check():
    """Comprehensive health check including orchestrator"""
    try:
        # KB health
        kb = kbs.get(DEFAULT_PROJECT)
        kb_stats = kb.get_stats()

        # Orchestrator health
        orchestrator_health = orchestrator.health_check()
        queue_stats = orchestrator.get_queue_stats()

        return {
            "instance_id": INSTANCE_ID,
            "service_type": "kb",
            "status": "healthy",
            "service_name": "rest",
            "timestamp": datetime.utcnow().isoformat(),
            "orchestrator": {
                "type": ORCHESTRATOR_TYPE,
                "identity": ORCHESTRATOR_IDENTITY,
                "health": orchestrator_health,
                "queue_stats": queue_stats
            },
            "kb_stats": kb_stats,
            "storage_path": f"{KDCUBE_STORAGE_PATH}/kb"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@app.get("/api/kb/health/process")
async def kb_process_health():
    """Health check for this specific KB process"""
    return {
        "instance_id": INSTANCE_ID,
        "process_id": os.getpid(),
        "port": KB_PORT,
        "service_type": "kb",
        "service_name": "rest",
        "current_load": heartbeat_manager.current_load,
        "max_capacity": heartbeat_manager.max_capacity,
        "health_status": heartbeat_manager.health_status.value,
        "timestamp": time.time()
    }


# ---------------------------------------
# Mount Additional Routers
# ---------------------------------------
from kdcube_ai_app.apps.knowledge_base.api.search import mount_search_routers
from kdcube_ai_app.apps.knowledge_base.api.registry import mount_registry_routers
from kdcube_ai_app.apps.orchestration.api import mount_orchestrator_routers

auth_manager = get_idp()
mount_search_routers(app, get_kb_for_project, auth_manager)
mount_registry_routers(app, get_kb_for_project, auth_manager)
mount_orchestrator_routers(app, get_kb_for_project, auth_manager)
# ---------------------------------------
# Run
# ---------------------------------------

if __name__ == "__main__":
    uvicorn.run(app,
                host="0.0.0.0",
                port=int(os.getenv("KB_APP_PORT", KB_PORT)),
                lifespan="on")
