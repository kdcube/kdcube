# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# infra/namespaces.py
import os

from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, EMBEDDERS, DEFAULT_MODEL_CONFIG, DEFAULT_EMBEDDER_CONFIG


def tp_prefix(tenant: str | None = None, project: str | None = None) -> str:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    s = get_settings()
    t = tenant or s.TENANT
    p = project or s.PROJECT
    return f"{t}:{p}"

def ns_key(base: str, *, tenant: str | None = None, project: str | None = None) -> str:
    return f"{tp_prefix(tenant, project)}:{base}"

class REDIS:
    class CHAT:
        PROMPT_QUEUE_PREFIX = "kdcube:chat:prompt:queue"
        PROMPT_QUEUE_INFLIGHT_PREFIX = "kdcube:chat:prompt:queue:inflight"
        CONVERSATION_MAILBOX_PREFIX = "kdcube:chat:conversation:mailbox"
        CONVERSATION_MAILBOX_SEQ_PREFIX = "kdcube:chat:conversation:mailbox:seq"
        CONVERSATION_MAILBOX_COUNT_PREFIX = "kdcube:chat:conversation:mailbox:count"
        SSE_CONNECTIONS_PREFIX = "kdcube:chat:sse:connections"

    class INSTANCE:
        HEARTBEAT_PREFIX = "kdcube:heartbeat:instance"

    class PROCESS:
        HEARTBEAT_PREFIX = "kdcube:heartbeat:process"

    SESSION = "kdcube:session"

    class THROTTLING:
        EVENTS_KEY = "kdcube:throttling:events"
        STATS_KEY = "kdcube:throttling:stats"
        SESSION_COUNTERS_KEY = "kdcube:throttling:session_counters"
        TOTAL_REQUESTS_KEY = "kdcube:throttling:total_requests"
        TOTAL_REQUESTS_HOURLY = "kdcube:throttling:requests:hourly"
        TOTAL_THROTTLED_REQUESTS_KEY = "kdcube:throttling:total_throttled"
        RATE_LIMIT_429 = "kdcube:throttling:rate_limit_429"
        BACKPRESSURE_503 = "kdcube:throttling:backpressure_503"
        HOURLY = "kdcube:throttling:hourly"
        BY_REASON = "kdcube:throttling:by_reason"

    class CIRCUIT_BREAKER:
        """Circuit breaker Redis keys"""
        PREFIX = "kdcube:circuit_breaker"
        STATE_SUFFIX = "state"          # {PREFIX}:{name}:state
        STATS_SUFFIX = "stats"          # {PREFIX}:{name}:stats
        WINDOW_SUFFIX = "window"        # {PREFIX}:{name}:window
        HALF_OPEN_SUFFIX = "half_open_calls"  # {PREFIX}:{name}:half_open_calls

        # Global circuit breaker stats
        GLOBAL_STATS = "kdcube:circuit_breaker:global_stats"
        EVENTS_LOG = "kdcube:circuit_breaker:events"

    class ECONOMICS:
        RATE_LIMIT = "kdcube:economics:rl"
        PROJ_BUDGET = "kdcube:economics:proj.budget"
        PLAN_BALANCE_CACHE = "kdcube:economics:plan.balance"

    class SYSTEM:
        CAPACITY = "kdcube:system:capacity"
        RATE_LIMIT = "kdcube:system:ratelimit"

    class METRICS:
        POOL_UTILIZATION = "kdcube:metrics:pool_utilization"
        POOL_IN_USE = "kdcube:metrics:pool_in_use"
        QUEUE_PRESSURE = "kdcube:metrics:queue_pressure"
        QUEUE_DEPTH = "kdcube:metrics:queue_depth"
        SSE_CONNECTIONS = "kdcube:metrics:sse_connections"
        TASK_QUEUE_WAIT_MS = "kdcube:metrics:task_queue_wait_ms"
        TASK_EXEC_MS = "kdcube:metrics:task_exec_ms"
        INGRESS_REST_MS = "kdcube:metrics:ingress_rest_ms"

    class CACHE:
        FAVICON = "kdcube:cache:favicon"
        MCP = "kdcube:cache:mcp"

    class SYNCHRONIZATION:
        LOCK = "kdcube:lock"

    class DISCOVERY:
        REGISTRY = "kdcube:registry"

class CONFIG:
    ID_TOKEN_HEADER_NAME = os.getenv("ID_TOKEN_HEADER_NAME", "X-ID-Token")
    STREAM_ID_HEADER_NAME = os.getenv("STREAM_ID_HEADER_NAME", "KDC-Stream-ID")
    USER_TIMEZONE_HEADER_NAME = os.getenv("USER_TIMEZONE_HEADER_NAME", "X-User-Timezone")
    USER_UTC_OFFSET_MIN_HEADER_NAME = os.getenv("USER_UTC_OFFSET_MIN_HEADER_NAME", "X-User-UTC-Offset")
    AUTH_TOKEN_COOKIE_NAME = os.getenv("AUTH_TOKEN_COOKIE_NAME", "__Secure-LATC")
    ID_TOKEN_COOKIE_NAME = os.getenv("ID_TOKEN_COOKIE_NAME", "__Secure-LITC")

    class BUNDLES:
        BUNDLE_MAPPING_KEY_FMT = "kdcube:config:bundles:mapping:{tenant}:{project}"
        UPDATE_CHANNEL = "kdcube:config:bundles:update:{tenant}:{project}"
        PROPS_KEY_FMT = "kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}"
        PROPS_UPDATE_CHANNEL = "kdcube:config:bundles:props:update:{tenant}:{project}"
        SECRETS_KEYS_FMT = "kdcube:config:bundles:secrets:{tenant}:{project}:{bundle_id}"
        SECRETS_FILE_LOCK_FMT = "kdcube:config:bundles:secrets:file:lock:{tenant}:{project}"
        CLEANUP_CHANNEL = "kdcube:config:bundles:cleanup:{tenant}:{project}"
        ACTIVE_REFS_KEY_FMT = "kdcube:config:bundles:refs:{tenant}:{project}"
        ENV_SYNC_LOCK_FMT = "kdcube:config:bundles:env-sync-lock:{tenant}:{project}"
        PRELOAD_LOCK_FMT = "kdcube:config:bundles:preload-lock:{tenant}:{project}"

    class GATEWAY:
        NAMESPACE = "kdcube:config:gateway"
        UPDATE_CHANNEL = "kdcube:config:gateway:update"
        CURRENT_KEY = "current"

    class AGENTIC:
        DEFAULT_LLM_MODEL_CONFIG = MODEL_CONFIGS.get(os.getenv("DEFAULT_LLM_MODEL_ID")) or DEFAULT_MODEL_CONFIG
        DEFAULT_EMBEDDING_MODEL_CONFIG = EMBEDDERS.get(os.getenv("DEFAULT_EMBEDDING_MODEL_ID")) or DEFAULT_EMBEDDER_CONFIG

        SUGGESTIONS_PREFIX = "kdcube:agentic:suggestions:{tenant}:{project}:{bundle_id}"

    KDCUBE_STORAGE_PATH = os.environ.get("KDCUBE_STORAGE_PATH") or os.environ.get("STORAGE_PATH")
