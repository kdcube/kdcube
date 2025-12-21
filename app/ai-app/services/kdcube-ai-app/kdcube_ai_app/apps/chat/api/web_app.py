# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/web_app.py
"""
FastAPI chat application with modular Socket.IO integration and gateway protection
"""
import traceback

import time
import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from dotenv import load_dotenv, find_dotenv

from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.infra.plugin.agentic_loader import AgenticBundleSpec

load_dotenv(find_dotenv())

import kdcube_ai_app.apps.utils.logging_config as logging_config
logging_config.configure_logging()

from kdcube_ai_app.infra.rendering.link_preview import close_shared_link_preview
from kdcube_ai_app.infra.rendering.shared_browser import close_shared_browser

from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator, ChatCommunicator

from kdcube_ai_app.apps.middleware.gateway import STATE_FLAG, STATE_SESSION, STATE_USER_TYPE
from kdcube_ai_app.infra.gateway.backpressure import create_atomic_chat_queue_manager
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerError
from kdcube_ai_app.infra.gateway.config import get_gateway_config
from kdcube_ai_app.infra.namespaces import CONFIG

# Import our simplified components
from kdcube_ai_app.apps.chat.api.resolvers import (
    get_fastapi_adapter, get_fast_api_accounting_binder, get_user_session_dependency,
    INSTANCE_ID, CHAT_APP_PORT, REDIS_URL, auth_without_pressure, get_tenant, _announce_startup,
    get_pg_pool, get_conversation_system
)
from kdcube_ai_app.auth.sessions import UserType, UserSession
from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, EMBEDDERS

from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest

from kdcube_ai_app.apps.chat.api.socketio.chat import create_socketio_chat_handler
from kdcube_ai_app.apps.chat.api.sse.chat import create_sse_router, SSEHub

logger = logging.getLogger(__name__)


# ================================
# APPLICATION SETUP
# ================================
# CORS setup
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:4000",
    "http://localhost:5173",
    "http://localhost:8050",
    "http://localhost:5175",
]
app_domain = os.environ.get("APP_DOMAIN")
if app_domain:
    allowed_origins.append(f"https://{app_domain}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Simplified lifespan management"""
    # Startup
    logger.info(f"Chat service starting on port {CHAT_APP_PORT}")

    # mark not shutting down yet
    app.state.shutting_down = False

    # Initialize gateway adapter and store in app state
    app.state.gateway_adapter = get_fastapi_adapter()
    gateway_config = get_gateway_config()
    app.state.chat_queue_manager = create_atomic_chat_queue_manager(
        gateway_config.redis_url,
        gateway_config,
        app.state.gateway_adapter.gateway.throttling_monitor  # Pass throttling monitor
    )
    app.state.acc_binder = get_fast_api_accounting_binder()

    # --- Heartbeats / processor (uses local queue processor) ---
    from kdcube_ai_app.apps.chat.api.resolvers import get_heartbeats_mgr_and_middleware, get_external_request_processor, \
        service_health_checker

    app.state.chat_comm = ChatRelayCommunicator(redis_url=REDIS_URL,
                                                channel="chat.events",
                                                )
    app.state.pg_pool = await get_pg_pool()

    port = CHAT_APP_PORT
    process_id = os.getpid()

    async def agentic_app_func(comm_context: "ChatTaskPayload"):
        """
        Entry-point invoked by the processor. We do NOT bind a relay here.
        We receive a ready-to-use ChatCommunicator and pass it into the workflow.
        """
        import inspect
        from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle
        from kdcube_ai_app.infra.plugin.agentic_loader import get_workflow_instance
        from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config

        # config & bundle
        cfg_req = ConfigRequest(**(comm_context.config.values or {}))
        wf_config = create_workflow_config(cfg_req)
        bundle_id = comm_context.routing.bundle_id
        spec_resolved = resolve_bundle(bundle_id, override=None)

        wf_config.ai_bundle_spec = spec_resolved
        spec = AgenticBundleSpec(
            path=spec_resolved.path,
            module=spec_resolved.module,
            singleton=bool(spec_resolved.singleton),
        )
        workflow, create_initial_state_fn, _ = get_workflow_instance(
            spec=spec,
            config=wf_config,
            comm_context=comm_context,
            pg_pool=app.state.pg_pool,
            redis=app.state.middleware.redis
        )

        # set workflow state (no emits here; processor already announced start)
        state = {
            "request_id": comm_context.request.request_id,
            "tenant": comm_context.actor.tenant_id,
            "project": comm_context.actor.project_id,
            "user": comm_context.user.user_id,
            "user_type": comm_context.user.user_type,
            "session_id": comm_context.routing.session_id,
            "conversation_id": (comm_context.routing.conversation_id or comm_context.routing.session_id),
            "text": comm_context.request.message or (comm_context.request.payload or {}).get("text") or "",
            "turn_id": comm_context.routing.turn_id,
            "history": comm_context.request.chat_history or [],
            "final_answer": "",
            "followups": [],
            "step_logs": [],
            "start_time": comm_context.meta.created_at,
        }
        if hasattr(workflow, "set_state"):
            maybe = workflow.set_state(state)
            if inspect.isawaitable(maybe):
                await maybe

        params = dict(comm_context.request.payload or {})
        if "text" not in params and comm_context.request.message:
            params["text"] = comm_context.request.message
        command = comm_context.request.operation or params.pop("command", None)

        try:
            result = await (getattr(workflow, command)(**params) if (command and hasattr(workflow, command))
                            else workflow.run(**params))
            return result or {}
        except Exception as e:
            # Let processor send the error envelope; we just surface the message up.
            logger.error(traceback.format_exc())
            if not isinstance(e, EconomicsLimitException):
                return { "error_message": str(e), "final_answer": "An error occurred." }


    # ================================
    # SOCKET.IO SETUP
    # ================================

    # Create modular Socket.IO chat handler. Share communicator & queue manager.
    try:
        socketio_handler = create_socketio_chat_handler(
            app=app,
            gateway_adapter=app.state.gateway_adapter,
            chat_queue_manager=app.state.chat_queue_manager,
            allowed_origins=allowed_origins,
            instance_id=INSTANCE_ID,
            redis_url=REDIS_URL,
            chat_comm=app.state.chat_comm,
        )

        # Mount Socket.IO app if available
        socket_asgi_app = socketio_handler.get_asgi_app()
        if socket_asgi_app:
            app.mount("/socket.io", socket_asgi_app)
            app.state.socketio_handler = socketio_handler
            logger.info("Socket.IO chat handler mounted successfully")
        else:
            logger.warning("Socket.IO not available - chat handler disabled")

    except Exception as e:
        logger.error(f"Failed to setup Socket.IO chat handler: {e}")
        app.state.socketio_handler = None

    app.state.sse_hub = SSEHub(app.state.chat_comm)

    # Mount SSE routes (same relay and queue manager as Socket.IO)
    try:
        await app.state.sse_hub.start()
        sse_router = create_sse_router(
            app=app,
            gateway_adapter=app.state.gateway_adapter,
            chat_queue_manager=app.state.chat_queue_manager,
            instance_id=INSTANCE_ID,
            redis_url=REDIS_URL,
        )
        sse_router.state = app.state
        app.include_router(sse_router, prefix="/sse", tags=["SSE"])
        logger.info("SSE routes mounted at /sse")
        app.state.sse_enabled = True
    except Exception as e:
        logger.error(f"Failed to mount SSE routes: {e}")
        app.state.sse_enabled = False

    try:
        handler = agentic_app_func

        middleware, heartbeat_manager = get_heartbeats_mgr_and_middleware(port=port)
        health_checker = service_health_checker(middleware)

        # Store in app state for monitoring endpoints
        app.state.middleware = middleware
        app.state.heartbeat_manager = heartbeat_manager
        app.state.health_checker = health_checker

        # Reuse system components you already provision
        conversation_browser, conversation_index, conversation_store = await get_conversation_system(app.state.pg_pool)
        app.state.conversation_browser = conversation_browser
        app.state.conversation_index = conversation_index
        app.state.conversation_store = conversation_store

        processor = get_external_request_processor(middleware, handler, app)
        app.state.processor = processor

        # Start services
        await middleware.init_redis()
        await heartbeat_manager.start_heartbeat(interval=10)

        try:
            from kdcube_ai_app.infra.rendering.link_preview import get_shared_link_preview
            app.state.link_preview_instance = await get_shared_link_preview()

            await socketio_handler.start() # communicator subscribes internally
        except Exception as e:
            app.state.shared_browser_instance = None
            app.state.link_preview_instance = None
            logger.error(f"Failed to start chat relay listener: {e}")

        try:
            from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store_registry
            from kdcube_ai_app.infra.plugin.bundle_registry import set_registry as _set_mem_registry
            reg = await _load_store_registry(middleware.redis)
            bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
            _set_mem_registry(bundles_dict, reg.default_bundle_id)
            logger.info(f"Bundles registry loaded from Redis: {len(bundles_dict)} items (default={reg.default_bundle_id})")
        except Exception as e:
            logger.warning(f"Failed to load bundles registry from Redis; using env-only registry. {e}")

        await processor.start_processing()
        await health_checker.start_monitoring()

        logger.info(f"Chat process {process_id} started with enhanced gateway")
        _announce_startup()

    except Exception as e:
        logger.warning(f"Could not start legacy middleware: {e}")

    yield

    # mark shutdown so SSE generators can exit
    app.state.shutting_down = True

    # Shutdown
    if hasattr(app.state, "socketio_handler") and getattr(app.state.socketio_handler, "stop", None):
        try:
            await app.state.socketio_handler.stop()
        except Exception:
            pass
    await app.state.sse_hub.stop()
    if hasattr(app.state, 'heartbeat_manager'):
        await app.state.heartbeat_manager.stop_heartbeat()
    if hasattr(app.state, 'processor'):
        await app.state.processor.stop_processing()
    if hasattr(app.state, 'health_checker'):
        await app.state.health_checker.stop_monitoring()

    if hasattr(app.state, 'pg_pool'):
        await app.state.pg_pool.close()

    await close_shared_link_preview()
    await close_shared_browser()

    logger.info("Chat service stopped")


# Create FastAPI app
app = FastAPI(
    title="Chat API with Modular Socket.IO",
    description="Chat API with gateway integration and modular real-time Socket.IO streaming",
    lifespan=lifespan
)

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================
# MIDDLEWARE
# ================================

@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path.startswith(("/profile", "/monitoring", "/admin", "/health", "/docs", "/openapi.json", "/favicon.ico")):
        return await call_next(request)

    if getattr(request.state, STATE_FLAG, False):
        return await call_next(request)

    try:
        # FOR SSE: Check query params for auth tokens if headers are missing
        if request.url.path.startswith("/sse/"):
            bearer_token = request.query_params.get("bearer_token")
            id_token = request.query_params.get("id_token")
            user_timezone = request.query_params.get("user_timezone")
            user_utc_offset_min = request.query_params.get("user_utc_offset_min")

            # Inject into headers so gateway adapter can process normally
            if bearer_token and "authorization" not in request.headers:
                request._headers = dict(request.headers)
                request._headers["authorization"] = f"Bearer {bearer_token}"
            request._headers = dict(request.headers) if not hasattr(request, '_headers') else request._headers
            if id_token:
                # request._headers[id_token_header.lower()] = id_token
                request._headers[CONFIG.ID_TOKEN_HEADER_NAME] = id_token
            if user_timezone:
                request._headers[CONFIG.USER_TIMEZONE_HEADER_NAME] = user_timezone
            if user_utc_offset_min:
                request._headers[CONFIG.USER_UTC_OFFSET_MIN_HEADER_NAME] = user_utc_offset_min

        # session = await app.state.gateway_adapter.process_request(request, [])
        session = await app.state.gateway_adapter.process_by_policy(request)
        setattr(request.state, STATE_SESSION, session)
        setattr(request.state, STATE_USER_TYPE, session.user_type.value)
        setattr(request.state, STATE_FLAG, True)

        response = await call_next(request)

        response.headers["X-User-Type"] = session.user_type.value
        response.headers["X-Session-ID"] = session.session_id
        return response
    except HTTPException as e:
        headers = getattr(e, "headers", {})
        return JSONResponse(
            status_code=e.status_code,
            content=e.detail if isinstance(e.detail, dict) else {"detail": e.detail},
            headers=headers
        )

# ================================
# ENDPOINTS
# ================================
@app.get("/")
async def root():
    """Root endpoint with API information"""
    socketio_enabled = hasattr(app.state, 'socketio_handler') and app.state.socketio_handler is not None

    return {
        "name": "KDCube AI App Platform",
        "version": "3.0.0",
        "description": "Multitenant hosting for your AI applications",
        "features": [
        ],
        "available_models": list(MODEL_CONFIGS.keys()),
        "socketio_enabled": socketio_enabled,
        "endpoints": {
        }
    }


@app.post("/landing/test-embeddings")
async def check_embeddings_endpoint(request: ConfigRequest,
                                    session: UserSession = Depends(auth_without_pressure())):
    """Test embedding configuration"""
    try:
        from kdcube_ai_app.infra.service_hub.inventory import probe_embeddings
        return probe_embeddings(request)
    except Exception as e:
        import traceback
        logger.error(f"Error testing embeddings: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Error testing embeddings: {str(e)}",
                "embedder_id": request.selected_embedder
            }
        )

@app.get("/profile")
# think of replacing with auth_without_pressure
async def get_profile(session: UserSession = Depends(get_user_session_dependency())):
    """Get user profile - works for both anonymous and registered users"""
    if session.user_type in [UserType.REGISTERED, UserType.PRIVILEGED]:
        return {
            "user_type": "registered" if session.user_type == UserType.REGISTERED else "privileged",
            "username": session.username,
            "user_id": session.user_id,
            "roles": session.roles,
            "permissions": session.permissions,
            "session_id": session.session_id,
            "created_at": session.created_at
        }
    else:
        return {
            "user_type": "anonymous",
            "fingerprint": session.fingerprint[:8] + "...",
            "session_id": session.session_id,
            "created_at": session.created_at
        }

@app.get("/landing/models")
async def get_available_models(session: UserSession = Depends(get_user_session_dependency())):
    """Get available model configurations"""
    return {
        "available_models": {
            model_id: {
                "id": model_id,
                "name": config["model_name"],
                "provider": config["provider"],
                "has_classifier": config["has_classifier"],
                "description": config["description"]
            }
            for model_id, config in MODEL_CONFIGS.items()
        },
        "default_model": "gpt-4o"
    }

@app.get("/landing/embedders")
async def get_available_embedders(session: UserSession = Depends(get_user_session_dependency())):
    """Get available embedding configurations"""
    available_embedders = {
        "available_embedders": {
            embedder_id: {
                "id": embedder_id,
                "provider": config["provider"],
                "model": config["model_name"],
                "dimension": config["dim"],
                "description": config["description"]
            }
            for embedder_id, config in EMBEDDERS.items()
        },
        "default_embedder": "openai-text-embedding-3-small",
        "providers": {
            "openai": {
                "name": "OpenAI",
                "description": "OpenAI's embedding models",
                "requires_api_key": True,
                "requires_endpoint": False
            },
            "custom": {
                "name": "Custom/HuggingFace",
                "description": "Custom embedding endpoints (HuggingFace, etc.)",
                "requires_api_key": False,
                "requires_endpoint": True
            }
        }
    }
    return available_embedders

# ================================
# MONITORING ENDPOINTS
# ================================

@app.get("/health")
async def health_check():
    """Basic health check"""
    socketio_status = "enabled" if hasattr(app.state, 'socketio_handler') and app.state.socketio_handler else "disabled"
    sse_status = "enabled" if  hasattr(app.state, 'sse_enabled') and app.state.sse_enabled else "disabled"
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "instance_id": INSTANCE_ID,
        "port": CHAT_APP_PORT,
        "socketio_status": socketio_status,
        "sse_status": sse_status,
    }

@app.get("/debug/session")
async def debug_session(session: UserSession = Depends(get_user_session_dependency())):
    """Debug endpoint to see current session"""
    return {
        "session": session.__dict__,
        "user_type": session.user_type.value
    }

# ================================
# ERROR HANDLERS
# ================================

@app.exception_handler(Exception)
async def exception_handler(request: Request, exc: Exception):
    """Enhanced exception handler that records circuit breaker failures"""
    logger.exception(f"Unhandled exception in {request.url.path}: {exc}")

    # Record failure in appropriate circuit breakers if it's a service error
    if hasattr(app.state, 'gateway_adapter'):
        try:
            # You could record failures in relevant circuit breakers here
            # based on the type of exception and endpoint
            pass
        except Exception as cb_error:
            logger.error(f"Error recording circuit breaker failure: {cb_error}")

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


@app.exception_handler(CircuitBreakerError)
async def circuit_breaker_exception_handler(request: Request, exc: CircuitBreakerError):
    """Handle circuit breaker errors gracefully"""
    logger.warning(f"Circuit breaker '{exc.circuit_name}' blocked request to {request.url.path}")

    return JSONResponse(
        status_code=503,
        content={
            "detail": "Service temporarily unavailable due to circuit breaker",
            "circuit_breaker": exc.circuit_name,
            "retry_after": exc.retry_after,
            "message": "The service is experiencing issues and is temporarily unavailable. Please try again later."
        },
        headers={"Retry-After": str(exc.retry_after)}
    )


# Mount monitoring routers
from kdcube_ai_app.apps.chat.api.monitoring import mount_monitoring_routers
mount_monitoring_routers(app)

# Mount integrations router
from kdcube_ai_app.apps.chat.api.integrations import mount_integrations_routers
mount_integrations_routers(app)

# Mount resources router
from kdcube_ai_app.apps.chat.api.resources import mount_resources_router
mount_resources_router(app)

# Mount conversations router
from kdcube_ai_app.apps.chat.api.conversations import mount_conversations_router
mount_conversations_router(app)

# Mount opex router
from kdcube_ai_app.apps.chat.api.opex import mount_opex_router
mount_opex_router(app)

# Mount Control Plane router
from kdcube_ai_app.apps.chat.api.control_plane import mount_control_plane_router
mount_control_plane_router(app)

# ================================
# RUN APPLICATION
# ================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=CHAT_APP_PORT,
        log_config=None,   # ← don't let Uvicorn install its own handlers
        log_level=None,
        timeout_keep_alive=60*60, # TODO : DO NOT FORGET TO REMOVE THIS
        # timeout_keep_alive=45
    )
