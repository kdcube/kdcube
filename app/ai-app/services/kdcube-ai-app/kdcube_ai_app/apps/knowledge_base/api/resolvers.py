# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# knowledge_base/api/resolvers.py
# Updated KB resolvers with new auth system
from fastapi import Request, Depends
import os
import logging

from kdcube_ai_app.apps.knowledge_base.core import KnowledgeBase
from kdcube_ai_app.apps.middleware.accounting import MiddlewareAuthWithAccounting
# Import new auth system
from kdcube_ai_app.apps.middleware.simple_idp import SimpleIDP
from kdcube_ai_app.auth.AuthManager import AuthManager, RequirementBase, RequireUser, RequireRoles, RequirementValidationError

from kdcube_ai_app.apps.knowledge_base.db.providers.tenant_db import TenantDB
from kdcube_ai_app.apps.knowledge_base.tenant import TenantProjects
from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, EMBEDDERS
from kdcube_ai_app.auth.sessions import SessionManager

from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware, \
    ProcessHeartbeatManager
from kdcube_ai_app.infra.embedding.faiss_manager import FaissProjectCache
from kdcube_ai_app.infra.llm.util import get_service_key_fn
from kdcube_ai_app.infra.orchestration.orchestration import IOrchestrator, OrchestratorFactory
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.llm.llm_data_model import AIProvider, ModelRecord, AIProviderName

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = os.environ.get("DEFAULT_PROJECT_NAME", None)
DEFAULT_TENANT_ID = os.environ.get("DEFAULT_PROJECT_NAME", None)
TENANT_ID = os.environ.get("TENANT_ID", DEFAULT_TENANT_ID)

def get_project(request: Request) -> str:
    """Look for a `project` path-param; if absent, return your default_project."""
    return request.path_params.get("project", DEFAULT_PROJECT)

def get_tenant_dep(request: Request) -> str:
    """Look for a `project` path-param; if absent, return your default_project."""
    return request.path_params.get("tenant", TENANT_ID)


# Environment configuration
KDCUBE_STORAGE_PATH = os.environ.get("KDCUBE_STORAGE_PATH")

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"

STORAGE_KWARGS = {}  # or AWS creds for S3
storage_backend = create_storage_backend(f"{KDCUBE_STORAGE_PATH}/kb", **STORAGE_KWARGS)
kdcube_storage_backend = create_storage_backend(KDCUBE_STORAGE_PATH, **STORAGE_KWARGS)

print(f"STORAGE_PATH={KDCUBE_STORAGE_PATH}")

def kb_workdir(tenant: str, project: str):
    w = f"{KDCUBE_STORAGE_PATH}/kb/tenants/{tenant}/projects/{project}/knowledge_base"
    print(f"Project workdir: {w}")
    return w

def metadata_model() -> ModelRecord:
    provider = AIProviderName.open_ai
    provider = AIProvider(provider=provider,
                          apiToken=get_service_key_fn(provider))
    model_config = MODEL_CONFIGS.get("gpt-4o", {})
    model_name = model_config.get("model_name")

    model_record = ModelRecord(modelType="base",
                               status="active",
                               provider=provider,
                               systemName=model_name)
    return model_record

def embedding_model() -> ModelRecord:
    provider = AIProviderName.open_ai
    provider = AIProvider(provider=provider,
                          apiToken=get_service_key_fn(provider))
    model_config = EMBEDDERS.get("openai-text-embedding-3-small")
    model_name = model_config.get("model_name")
    return ModelRecord(
        modelType="base",
        status="active",
        provider=provider,
        systemName=model_name,
    )

_cache = FaissProjectCache(max_loaded=5, redis_url=REDIS_URL, storage=storage_backend)

def get_faiss_cache() -> FaissProjectCache:
    """Singleton cache."""
    return _cache

def get_faiss_index(
        project: str = Depends(get_project),
        cache: FaissProjectCache = Depends(get_faiss_cache),
):
    """
    Yields a FAISS index for the given project, automatically
    acquiring and releasing the internal lock/ref-count.
    """
    usage = cache.get(project)
    idx = usage.__enter__()   # load or grab cached mmap
    try:
        yield idx
    finally:
        usage.__exit__(None, None, None)

# Orchestrator setup
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

# System configuration
ENABLE_DATABASE = os.environ.get("ENABLE_DATABASE", "true").lower() == "true"
INSTANCE_ID = os.environ.get("INSTANCE_ID", "home-instance-1")
KB_PORT = int(os.environ.get("KB_PORT", 8000))
KB_PARALLELISM = int(os.environ.get("KB_PARALLELISM", 1))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", 10))

DEFAULT_PROJECT = os.environ.get("DEFAULT_PROJECT_NAME", None)
# Database setup
_tenant_db = TenantDB(tenant_id=TENANT_ID) if ENABLE_DATABASE else None

def get_tenant():
    return TENANT_ID

def get_tenant_db() -> TenantDB:
    """Singleton TenantDB instance."""
    if not _tenant_db:
        raise RuntimeError("TenantDB not available (database support disabled)")
    return _tenant_db

# Tenant projects setup
_tenant_projects = TenantProjects(
    storage_backend=storage_backend,
    tenant_db=_tenant_db,
    tenant_id=TENANT_ID,
    embedding_model_factory=embedding_model
)

def get_tenant_projects() -> TenantProjects:
    """Singleton TenantProjects instance."""
    return _tenant_projects

def get_system_health():
    """Get comprehensive system health."""
    try:
        # Orchestrator health
        orchestrator_health = get_orchestrator().health_check()
        queue_stats = get_orchestrator().get_queue_stats()

        # Tenant health
        tenant_health = get_tenant_projects().get_tenant_health()

        # FAISS cache health
        faiss_health = {
            "status": "healthy",
            "loaded_projects": len(get_faiss_cache()._loaded),
            "max_loaded": get_faiss_cache().max_loaded
        }

        overall_status = "healthy"
        if (tenant_health.get("status") != "healthy" or
                orchestrator_health.get("status") != "healthy"):
            overall_status = "degraded"

        return {
            "status": overall_status,
            "tenant_id": TENANT_ID,
            "components": {
                "orchestrator": {
                    "type": ORCHESTRATOR_TYPE,
                    "identity": ORCHESTRATOR_IDENTITY,
                    "health": orchestrator_health,
                    "queue_stats": queue_stats
                },
                "tenant": tenant_health,
                "faiss_cache": faiss_health
            },
            "configuration": {
                "storage_path": KDCUBE_STORAGE_PATH,
                "database_enabled": ENABLE_DATABASE,
                "tenant_id": TENANT_ID
            }
        }

    except Exception as e:
        print(f"System health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "tenant_id": TENANT_ID
        }

def get_heartbeats_mgr_and_middleware(service_type: str = "kb",
                                      service_name: str = "rest",
                                      instance_id: str = None,
                                      port: int = 8000):

    instance_id = instance_id or os.getenv("INSTANCE_ID")
    middleware = MultiprocessDistributedMiddleware(REDIS_URL, instance_id=instance_id, tenant=TENANT_ID, project=DEFAULT_PROJECT)
    process_id = os.getpid()
    heartbeat_manager = ProcessHeartbeatManager(middleware, service_type, service_name, process_id, port=port)
    return middleware, heartbeat_manager

kbs = {
    DEFAULT_PROJECT: KnowledgeBase(get_tenant(),
                                   DEFAULT_PROJECT,
                                   kb_workdir(get_tenant(), DEFAULT_PROJECT),
                                   embedding_model=embedding_model()),
}


def get_kb_for_project(project: str) -> 'KnowledgeBase':
    kb = kbs.get(project)
    if not kb:
        kb = KnowledgeBase(get_tenant(), project, kb_workdir(get_tenant(), project), embedding_model=embedding_model())
        # raise HTTPException(status_code=404, detail=f"Knowledge base for project '{project}' not found")
    return kb

# ================================
# NEW AUTH SYSTEM SETUP
# ================================

SERVICE_ROLE_NAME = os.environ.get("SERVICE_ROLE_NAME", "kdcube:role:service")

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

# Singleton auth manager and adapter
session_manager = SessionManager(
    REDIS_URL,
    tenant=DEFAULT_TENANT_ID,
    project=DEFAULT_PROJECT
)
_auth_manager = None
_base_auth = None
_auth_with_acct = None

def get_kb_auth_manager() -> AuthManager:
    """Get singleton auth manager"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = create_auth_manager()
    return _auth_manager

def get_kb_fastapi_auth() -> 'FastAPIAuthAdapter':
    """Get singleton FastAPI auth adapter"""
    global _base_auth
    if _base_auth is None:
        from kdcube_ai_app.apps.middleware.auth import FastAPIAuthAdapter
        _base_auth = FastAPIAuthAdapter(auth_manager=get_kb_auth_manager(),
                                        service_role_name=SERVICE_ROLE_NAME,
                                        session_manager=session_manager)
    return _base_auth

# Legacy compatibility
def get_idp() -> AuthManager:
    """Legacy compatibility - returns the auth manager if it's SimpleIDP"""
    return get_kb_auth_manager()

# ================================
# KB-SPECIFIC AUTH ROLES AND REQUIREMENTS
# ================================

# Define KB-specific roles
class RequireKBAdmin(RequirementBase):
    """Require KB admin role"""
    def validate_requirement(self, user):
        if not user:
            return RequirementValidationError("User is required.", 401)

        admin_roles = [
            "kdcube:role:super-admin",
            "kdcube:role:kb-admin",
            "admin"  # Simple role for testing
        ]

        if not any(role in user.roles for role in admin_roles):
            return RequirementValidationError(
                f"KB admin access required. User has roles: {user.roles}", 403
            )
        return None

class RequireKBRead(RequirementBase):
    """Require KB read access"""
    def validate_requirement(self, user):
        if not user:
            return RequirementValidationError("User is required.", 401)

        read_roles = [
            "kdcube:role:super-admin",
            "kdcube:role:kb-admin",
            "kdcube:role:kb-read",
            "kdcube:role:chat-user",  # Chat users can read KB
            "admin",
            "user"  # Simple roles for testing
        ]

        if not any(role in user.roles for role in read_roles):
            return RequirementValidationError(
                f"KB read access required. User has roles: {user.roles}", 403
            )
        return None

class RequireKBWrite(RequirementBase):
    """Require KB write access (upload, modify resources)"""
    def validate_requirement(self, user):
        if not user:
            return RequirementValidationError("User is required.", 401)

        write_roles = [
            "kdcube:role:super-admin",
            "kdcube:role:kb-admin",
            "kdcube:role:kb-write",
            "admin"  # Simple role for testing
        ]

        if not any(role in user.roles for role in write_roles):
            return RequirementValidationError(
                f"KB write access required. User has roles: {user.roles}", 403
            )
        return None

# Export commonly used requirements - these return tuples of RequirementBase
def require_kb_admin():
    return RequireKBAdmin()

def require_kb_read():
    return RequireKBRead()

def require_kb_write():
    return RequireKBWrite()

def require_system_admin():
    return RequireRoles("kdcube:role:super-admin", "admin")

# Export FastAPI dependencies - these return actual FastAPI dependencies
def get_kb_admin_dep():
    """FastAPI dependency that requires KB admin access"""
    return get_kb_fastapi_auth().require(*require_kb_admin())

def get_kb_read_dep():
    """FastAPI dependency that requires KB read access"""
    return get_kb_fastapi_auth().require(*require_kb_read())

def get_kb_write_dep():
    """FastAPI dependency that requires KB write access"""
    return get_kb_fastapi_auth().require(*require_kb_write())

def get_system_admin_dep():
    """FastAPI dependency that requires system admin access"""
    return get_kb_fastapi_auth().require(*require_system_admin())

ACCOUNTING_ENABLED = os.environ.get("ACCOUNTING_ENABLED", "true").lower() == "true"

def get_kb_auth_with_accounting() -> MiddlewareAuthWithAccounting:
    global _auth_with_acct
    if _auth_with_acct is None:
        _auth_with_acct = MiddlewareAuthWithAccounting(
            base_auth_adapter=get_kb_fastapi_auth(),
            get_tenant_fn=get_tenant,
            storage_backend=kdcube_storage_backend,
            accounting_enabled=ACCOUNTING_ENABLED,
            default_component="kb-rest",
        )
    return _auth_with_acct

# Expose deps that mirror your role requirements but also set accounting context
def get_kb_write_with_acct_dep():
    return get_kb_auth_with_accounting().require_auth_with_accounting(*require_kb_write())

def get_kb_read_with_acct_dep():
    return get_kb_auth_with_accounting().require_auth_with_accounting(*require_kb_read())

def get_kb_admin_with_acct_dep():
    return get_kb_auth_with_accounting().require_auth_with_accounting(*require_kb_admin())