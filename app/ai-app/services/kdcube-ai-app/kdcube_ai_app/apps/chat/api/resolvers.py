# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/api/resolvers.py
"""
Simplified resolvers module with clean separation of concerns
"""
import os
import logging
from typing import Tuple, Optional

from starlette.requests import Request

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
# Import centralized configuration
from kdcube_ai_app.infra.gateway.config import (
    GatewayConfigFactory,
    GatewayConfiguration,
    GatewayProfile,
    get_gateway_config,
    set_gateway_config,
    PRESET_CONFIGURATIONS
)
from kdcube_ai_app.infra.gateway.gateway import create_gateway_from_config

from kdcube_ai_app.apps.middleware.gateway import FastAPIGatewayAdapter
from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, ModelServiceBase, create_workflow_config

logger = logging.getLogger(__name__)

# Environment configuration
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"

TENANT_ID = os.environ.get("TENANT_ID", "home")
INSTANCE_ID = os.environ.get("INSTANCE_ID", "home-instance-1")
CHAT_APP_PORT = int(os.environ.get("CHAT_APP_PORT", 8010))

# Gateway Profile Selection
GATEWAY_PROFILE = os.environ.get("GATEWAY_PROFILE", "development").lower()
GATEWAY_PRESET = os.environ.get("GATEWAY_PRESET", None)  # Optional preset override

# Storage and other configurations (your existing logic)

_settings = get_settings()
# STORAGE_PATH = os.environ.get("KDCUBE_STORAGE_PATH")
STORAGE_PATH = _settings.STORAGE_PATH

DEFAULT_PROJECT = os.environ.get("DEFAULT_PROJECT_NAME", None)

def get_project(request: Request) -> str:
    """Look for a `project` path-param; if absent, return default_project."""
    if hasattr(request, 'path_params'):
        return request.path_params.get("project", DEFAULT_PROJECT)
    return DEFAULT_PROJECT

def get_tenant_dep(request: Request) -> str:
    """Look for a `tenant` path-param; if absent, return TENANT_ID."""
    if hasattr(request, 'path_params'):
        return request.path_params.get("tenant", TENANT_ID)
    return TENANT_ID

# Your existing storage, orchestrator, and model configurations
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.orchestration.orchestration import IOrchestrator, OrchestratorFactory

# Storage setup (your existing logic)
STORAGE_KWARGS = {}
storage_backend = create_storage_backend(STORAGE_PATH, **STORAGE_KWARGS)
logger.info(f"STORAGE_PATH={STORAGE_PATH}")

def workdir(tenant: str, project: str):
    w = f"{STORAGE_PATH}/cb/tenants/{tenant}/projects/{project}/chat"
    logger.info(f"Project workdir: {w}")
    return w

# Orchestrator setup (your existing logic)
ORCHESTRATOR_TYPE = os.environ.get("ORCHESTRATOR_TYPE", "dramatiq")
DEFAULT_ORCHESTRATOR_IDENTITY = f"kdcube_orchestrator_{ORCHESTRATOR_TYPE}"
ORCHESTRATOR_IDENTITY = os.environ.get("ORCHESTRATOR_IDENTITY", DEFAULT_ORCHESTRATOR_IDENTITY)

orchestrator: IOrchestrator = OrchestratorFactory.create_orchestrator(
    orchestrator_type=ORCHESTRATOR_TYPE,
    redis_url=REDIS_URL,
    orchestrator_identity=ORCHESTRATOR_IDENTITY
)

def get_orchestrator() -> IOrchestrator:
    """Singleton orchestrator instance."""
    return orchestrator

# Database setup (your existing logic)
ENABLE_DATABASE = os.environ.get("ENABLE_DATABASE", "true").lower() == "true"

def get_tenant() -> str:
    return TENANT_ID

# Session analytics service
_session_analytics_service = None

def get_session_analytics_service():
    ENABLE_SESSION_ANALYTICS = True
    """Get session analytics service (for PostgreSQL session analytics)"""
    global _session_analytics_service
    if _session_analytics_service is None and ENABLE_SESSION_ANALYTICS:
        try:
            from session_analytics_models import SessionAnalyticsService
            # You'll need to configure the database connection here
            # db_session = create_your_db_session()
            # _session_analytics_service = SessionAnalyticsService(db_session)
            pass
        except ImportError:
            logger.info("Session analytics models not available")
    return _session_analytics_service

# ================================
# CENTRALIZED GATEWAY CONFIGURATION
# ================================

def create_gateway_configuration() -> GatewayConfiguration:
    """Create centralized gateway configuration"""

    # Check if a preset is specified
    if GATEWAY_PRESET and GATEWAY_PRESET in PRESET_CONFIGURATIONS:
        logger.info(f"Using gateway preset: {GATEWAY_PRESET}")
        config = PRESET_CONFIGURATIONS[GATEWAY_PRESET]()

        # Override with environment-specific values
        config.redis_url = REDIS_URL
        config.instance_id = INSTANCE_ID
        config.tenant_id = TENANT_ID

        return config

    # Create from profile and environment
    try:
        profile = GatewayProfile(GATEWAY_PROFILE)
    except ValueError:
        logger.warning(f"Invalid gateway profile '{GATEWAY_PROFILE}', using development")
        profile = GatewayProfile.DEVELOPMENT

    logger.info(f"Creating gateway configuration with profile: {profile.value}")

    config = GatewayConfigFactory.create_from_env(profile)

    # Override with specific environment values
    config.redis_url = REDIS_URL
    config.instance_id = INSTANCE_ID
    config.tenant_id = TENANT_ID

    return config

def create_auth_manager():
    """Create the authentication manager"""
    # You can switch between different auth managers here:

    provider = os.getenv("AUTH_PROVIDER", "simple").lower()
    if provider == "cognito":
        from kdcube_ai_app.auth.implementations.cognito import CognitoAuthManager
        logger.info("Using CognitoAuthManager for authentication")
        return CognitoAuthManager(send_validation_error_details=True)

    if provider == "oauth":
        # existing generic OAuth option (if you keep it)
        from kdcube_ai_app.auth.OAuthManager import OAuthManager, OAuth2Config
        logger.info("Using OAuth for authentication")

        # Option 2: OAuth (uncomment when needed)
        # from oauth_manager import OAuthManager, OAuth2Config
        # return OAuthManager(
        #     OAuth2Config(
        #         oauth2_issuer="http://localhost:8080/realms/kdcube-dev",
        #         oauth2_audience="kdcube-chat",
        #         oauth2_jwks_url="http://localhost:8080/realms/kdcube-dev/protocol/openid-connect/certs",
        #         oauth2_userinfo_url="http://localhost:8080/realms/kdcube-dev/protocol/openid-connect/userinfo",
        #         oauth2_introspection_url="http://localhost:8080/realms/kdcube-dev/protocol/openid-connect/token/introspect",
        #         introspection_client_id="kdcube-server-private",
        #         introspection_client_secret="<GET TOKEN FROM INTROSPECTION CLIENT>",
        #         verification_method="both"
        #     )
        # )
    # default for dev
    from kdcube_ai_app.apps.middleware.simple_idp import SimpleIDP
    logger.info("Using SimpleIDP for authentication")
    return SimpleIDP(send_validation_error_details=True, service_user_token=os.getenv("SERVICE_USER_TOKEN"))

    # Option 3: Anonymous only (for testing)
    # from kdcube_ai_app.auth.AnonymousAuthManager import AnonymousAuthManager
    # return AnonymousAuthManager()

def create_request_gateway():
    """Create the request gateway with centralized configuration"""
    auth_manager = create_auth_manager()
    gateway_config = create_gateway_configuration()

    # Set the global configuration for monitoring access
    set_gateway_config(gateway_config)

    # Log configuration summary
    logger.info("Gateway Configuration Summary:")
    logger.info(f"  Profile: {gateway_config.profile.value}")
    logger.info(f"  Instance: {gateway_config.instance_id}")
    logger.info(f"  Service Capacity: {gateway_config.service_capacity.concurrent_requests_per_instance} concurrent, "
                f"{gateway_config.service_capacity.avg_processing_time_seconds}s avg")
    logger.info(f"  Rate Limits: Anon={gateway_config.rate_limits.anonymous_hourly}/hr, "
                f"Reg={gateway_config.rate_limits.registered_hourly}/hr")
    logger.info(f"  Backpressure Thresholds: Anon={gateway_config.backpressure.anonymous_pressure_threshold}, "
                f"Reg={gateway_config.backpressure.registered_pressure_threshold}, "
                f"Hard={gateway_config.backpressure.hard_limit_threshold}")

    # Create gateway with centralized config
    gateway = create_gateway_from_config(gateway_config, auth_manager)

    return gateway

def create_fastapi_gateway_adapter():
    """Create FastAPI adapter for the gateway"""
    gateway = create_request_gateway()
    return FastAPIGatewayAdapter(gateway)

# ================================
# CONFIGURATION ACCESS FUNCTIONS
# ================================

def get_current_gateway_config() -> GatewayConfiguration:
    """Get the current gateway configuration"""
    return get_gateway_config()

def get_gateway_config_dict() -> dict:
    """Get gateway configuration as dictionary for API exposure"""
    config = get_gateway_config()
    return config.to_dict()

def get_gateway_profile() -> str:
    """Get current gateway profile"""
    config = get_gateway_config()
    return config.profile.value

def update_gateway_config(**kwargs):
    """Update gateway configuration parameters"""
    config = get_gateway_config()

    # Update rate limits
    if 'anonymous_hourly' in kwargs:
        config.rate_limits.anonymous_hourly = kwargs['anonymous_hourly']
    if 'registered_hourly' in kwargs:
        config.rate_limits.registered_hourly = kwargs['registered_hourly']

    # Update service capacity
    if 'concurrent_requests_per_instance' in kwargs:
        config.service_capacity.concurrent_requests_per_instance = kwargs['concurrent_requests_per_instance']
    if 'avg_processing_time_seconds' in kwargs:
        config.service_capacity.avg_processing_time_seconds = kwargs['avg_processing_time_seconds']

    # Update backpressure
    if 'anonymous_pressure_threshold' in kwargs:
        config.backpressure.anonymous_pressure_threshold = kwargs['anonymous_pressure_threshold']
    if 'registered_pressure_threshold' in kwargs:
        config.backpressure.registered_pressure_threshold = kwargs['registered_pressure_threshold']

    # Validate updated configuration
    from kdcube_ai_app.infra.gateway.config import validate_gateway_config
    issues = validate_gateway_config(config)
    if issues:
        logger.warning(f"Configuration update validation issues: {issues}")

    set_gateway_config(config)
    logger.info("Gateway configuration updated")

# ================================
# SINGLETON INSTANCES
# ================================

# Create singleton instances
_auth_manager = None
_gateway = None
_fastapi_adapter = None
_pg_pool: Optional = None

_conv_index: Optional[ConvIndex] = None
_conv_store: Optional[ConversationStore] = None
_conv_browser: Optional[ContextRAGClient] = None

def get_auth_manager():
    """Get singleton auth manager"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = create_auth_manager()
    return _auth_manager

def get_gateway():
    """Get singleton gateway"""
    global _gateway
    if _gateway is None:
        _gateway = create_request_gateway()
    return _gateway

def get_fastapi_adapter():
    """Get singleton FastAPI adapter"""
    global _fastapi_adapter
    if _fastapi_adapter is None:
        _fastapi_adapter = create_fastapi_gateway_adapter()
    return _fastapi_adapter

# ================================
# CONVENIENCE FUNCTIONS
# ================================

_fastapi_adapter_with_accounting = None

def get_fast_api_accounting_binder():
    """Get FastAPI dependency for user session (no auth requirements)"""
    global _fastapi_adapter_with_accounting
    if _fastapi_adapter_with_accounting is None:
        from kdcube_ai_app.apps.middleware.gateway import AccountingContextBinder
        _fastapi_adapter_with_accounting = AccountingContextBinder(
            gateway_adapter=get_fastapi_adapter(),
            storage_backend=storage_backend,
            get_tenant_fn=get_tenant,
            accounting_enabled=True,
            default_component="chat-rest",
        )
    return _fastapi_adapter_with_accounting

def get_user_session_dependency():
    """Get FastAPI dependency for user session (no auth requirements)"""
    adapter = get_fast_api_accounting_binder()
    # return adapter.get_user_session_dependency()
    return adapter.http_dependency("chat-rest")

def auth_without_pressure(requirements = None):
    """Get FastAPI dependency for auth without pressure (admin access)"""
    adapter = get_fastapi_adapter()
    return adapter.auth_without_pressure(requirements)

def require_auth(*requirements):
    """Get FastAPI dependency with auth requirements"""
    adapter = get_fastapi_adapter()
    return adapter.require(*requirements)

async def get_service_token():
    """Get FastAPI dependency with service token"""
    auth_manager = get_auth_manager()
    return await auth_manager.get_service_token()

# ================================
# CONFIGURATION ENDPOINTS SUPPORT
# ================================


# Legacy compatibility - your existing middleware setup
def get_heartbeats_mgr_and_middleware(service_type: str = "chat",
                                      service_name: str = "rest",
                                      instance_id: str = None,
                                      process_id: str = None,
                                      port: int = CHAT_APP_PORT):
    """Your existing middleware setup - can be kept for compatibility"""
    from kdcube_ai_app.infra.availability.health_and_heartbeat import (
        MultiprocessDistributedMiddleware, ProcessHeartbeatManager
    )

    instance_id = instance_id or INSTANCE_ID
    middleware = MultiprocessDistributedMiddleware(REDIS_URL, instance_id)
    heartbeat_manager = ProcessHeartbeatManager(
        middleware=middleware,
        service_type=service_type,
        service_name=service_name,
        process_id=process_id,
        port=port
    )
    return middleware, heartbeat_manager

def get_external_request_processor(middleware, chat_handler, app):
    from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
    return EnhancedChatRequestProcessor(
        middleware,
        chat_handler,                     # agentic workflow entrypoint
        relay=app.state.chat_comm,      # use the Redis relay communicator
        conversation_ctx=app.state.conversation_browser,
        max_concurrent=5,
        task_timeout_sec=900,
    )

def service_health_checker(middleware):
    """Your existing health checker"""
    from kdcube_ai_app.infra.availability.health_and_heartbeat import ServiceHealthChecker
    return ServiceHealthChecker(middleware)

# ================================ Circuit Breaker Management ===============================
async def get_circuit_breaker_stats():
    """Get circuit breaker statistics"""
    gateway = get_gateway()
    return await gateway.circuit_manager.get_all_stats()

async def reset_circuit_breaker(circuit_name: str):
    """Reset a specific circuit breaker"""
    gateway = get_gateway()
    await gateway.circuit_manager.reset_circuit_breaker(circuit_name)

# ================================ System stats ===============================
async def get_system_status():
    """Get enhanced system status including circuit breakers and configuration"""
    gateway = get_gateway()
    status = await gateway.get_system_status()

    # Add configuration information to status
    config = get_gateway_config()
    status["gateway_configuration"] = config.to_dict()

    return status

# ================================
# CONFIGURATION PRESETS MANAGEMENT
# ================================

def list_available_presets():
    """List available configuration presets"""
    return list(PRESET_CONFIGURATIONS.keys())

def apply_configuration_preset(preset_name: str):
    """Apply a configuration preset"""
    if preset_name not in PRESET_CONFIGURATIONS:
        raise ValueError(f"Unknown preset: {preset_name}. Available: {list_available_presets()}")

    config = PRESET_CONFIGURATIONS[preset_name]()
    config.redis_url = REDIS_URL
    config.instance_id = INSTANCE_ID
    config.tenant_id = TENANT_ID

    set_gateway_config(config)
    logger.info(f"Applied configuration preset: {preset_name}")

    # Reset singleton instances to pick up new config
    global _gateway, _fastapi_adapter
    _gateway = None
    _fastapi_adapter = None

def get_configuration_comparison(other_preset: str = None):
    """Compare current configuration with another preset"""
    current_config = get_gateway_config()

    if other_preset:
        if other_preset not in PRESET_CONFIGURATIONS:
            raise ValueError(f"Unknown preset: {other_preset}")
        other_config = PRESET_CONFIGURATIONS[other_preset]()
    else:
        # Compare with default development config
        other_config = GatewayConfigFactory.create_from_env(GatewayProfile.DEVELOPMENT)

    return {
        "current": current_config.to_dict(),
        "comparison": other_config.to_dict(),
        "differences": _find_config_differences(current_config.to_dict(), other_config.to_dict())
    }

def _find_config_differences(config1: dict, config2: dict, path: str = "") -> dict:
    """Find differences between two configuration dictionaries"""
    differences = {}

    for key in set(config1.keys()) | set(config2.keys()):
        current_path = f"{path}.{key}" if path else key

        if key not in config1:
            differences[current_path] = {"missing_in_current": config2[key]}
        elif key not in config2:
            differences[current_path] = {"missing_in_comparison": config1[key]}
        elif isinstance(config1[key], dict) and isinstance(config2[key], dict):
            sub_diffs = _find_config_differences(config1[key], config2[key], current_path)
            differences.update(sub_diffs)
        elif config1[key] != config2[key]:
            differences[current_path] = {
                "current": config1[key],
                "comparison": config2[key]
            }

    return differences

def _announce_startup():
    """Print a bold, clickable 'Application is running' line to stdout."""
    url = os.environ.get("CHAT_PUBLIC_URL")
    if not url:
        # fallback: infer from port if no env provided
        port = os.environ.get("CHAT_APP_PORT") or str(CHAT_APP_PORT)
        url = f"http://localhost:{port}/health"
    try:
        # Bold line
        print(f"\n\033[1mApplication is running:\033[0m {url}\n", flush=True)
        # Terminal hyperlink (OSC 8) — most modern terminals make this clickable
        print(f'\x1b]8;;{url}\x1b\\Open in browser\x1b]8;;\x1b\\\n', flush=True)
    except Exception:
        # be resilient; never crash on printing
        pass

async def get_pg_pool():
    _settings = get_settings()
    global _pg_pool

    if _pg_pool is not None:
        return _pg_pool

    import asyncpg, json
    async def _init_conn(conn: asyncpg.Connection):
        # Encode/decode json & jsonb as Python dicts automatically
        await conn.set_type_codec('json',  encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
        await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')

    _pg_pool = await asyncpg.create_pool(
        host=_settings.PGHOST,
        port=_settings.PGPORT,
        user=_settings.PGUSER,
        password=_settings.PGPASSWORD,
        database=_settings.PGDATABASE,
        ssl=_settings.PGSSL,
        init=_init_conn,
    )
    return _pg_pool

async def get_conversation_system(pg_pool) -> Tuple[ContextRAGClient, ConvIndex, ConversationStore]:

    _settings = get_settings()
    global _conv_index
    global _conv_store
    global _conv_browser

    if _conv_browser is not None:
        return _conv_browser, _conv_index, _conv_store

    if not pg_pool:
        raise Exception("[Conversation Browser]. PG pool not found")

    _conv_index = ConvIndex(pool=pg_pool)
    await _conv_index.init()

    req = ConfigRequest(
        openai_api_key=_settings.OPENAI_API_KEY,
        claude_api_key=_settings.ANTHROPIC_API_KEY,
        selected_model=_settings.DEFAULT_MODEL_LLM,
    )
    model_service = ModelServiceBase(create_workflow_config(req))
    _conv_store = ConversationStore(_settings.STORAGE_PATH)
    _conv_browser = ContextRAGClient(conv_idx=_conv_index,
                                  store=_conv_store,
                                  model_service=model_service)
    return _conv_browser, _conv_index, _conv_store

