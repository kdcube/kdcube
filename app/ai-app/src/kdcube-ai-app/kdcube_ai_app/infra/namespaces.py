# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# infra/namespaces.py

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
        CONVERSATION_EXTERNAL_EVENTS_PREFIX = "kdcube:chat:conversation:external-events"
        CONVERSATION_EXTERNAL_EVENTS_SEQ_PREFIX = "kdcube:chat:conversation:external-events:seq"
        CONVERSATION_TIMELINE_OWNER_PREFIX = "kdcube:chat:conversation:timeline-owner"
        SSE_CONNECTIONS_PREFIX = "kdcube:chat:sse:connections"

    class BACKGROUND:
        JOB_STREAM_PREFIX = "kdcube:background:jobs"
        JOB_DEDUPE_PREFIX = "kdcube:background:jobs:dedupe"

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
        # Query→vector, shared across workers: a search embeds the query on
        # every call, and the answer is a pure function of (query, model, dim).
        QUERY_EMBEDDING = "kdcube:cache:query-embedding"

    class SYNCHRONIZATION:
        LOCK = "kdcube:lock"

    class DISCOVERY:
        REGISTRY = "kdcube:registry"

class CONFIG:
    class PLATFORM_SETTINGS:
        UPDATE_CHANNEL = "kdcube:config:platform-settings:update:{tenant}:{project}"

    class SITES:
        CATALOG_KEY_FMT = "kdcube:config:sites:catalog:{{{tenant}:{project}}}"
        GENERATION_KEY_FMT = "kdcube:config:sites:generation:{{{tenant}:{project}}}"
        UPDATE_CHANNEL_FMT = "kdcube:config:sites:update:{tenant}:{project}"

    class BUNDLES:
        BUNDLE_MAPPING_KEY_FMT = "kdcube:config:bundles:mapping:{tenant}:{project}"
        UPDATE_CHANNEL = "kdcube:config:bundles:update:{tenant}:{project}"
        PROPS_KEY_FMT = "kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}"
        PROPS_UPDATE_CHANNEL = "kdcube:config:bundles:props:update:{tenant}:{project}"
        SECRETS_KEYS_FMT = "kdcube:config:bundles:secrets:{tenant}:{project}:{bundle_id}"
        USER_SECRETS_KEYS_FMT = "kdcube:config:bundles:user-secrets:{tenant}:{project}:{bundle_id}:{user_id}"
        SECRETS_UPDATE_CHANNEL = "kdcube:config:bundles:secrets:update:{tenant}:{project}"
        SECRETS_FILE_LOCK_FMT = "kdcube:config:bundles:secrets:file:lock:{tenant}:{project}"
        SECRETS_AWS_SM_LOCK_FMT = "kdcube:config:bundles:secrets:aws-sm:lock:{tenant}:{project}:{doc}"
        DESCRIPTORS_AWS_SM_LOCK_FMT = "kdcube:config:bundles:descriptor:aws-sm:lock:{tenant}:{project}:{doc}"
        CLEANUP_CHANNEL = "kdcube:config:bundles:cleanup:{tenant}:{project}"
        ACTIVE_REFS_KEY_FMT = "kdcube:config:bundles:refs:{tenant}:{project}"
        ENV_SYNC_LOCK_FMT = "kdcube:config:bundles:env-sync-lock:{tenant}:{project}"
        PRELOAD_LOCK_FMT = "kdcube:config:bundles:preload-lock:{tenant}:{project}"
        PRELOAD_BUNDLE_LOCK_FMT = "kdcube:config:bundles:preload-lock:{tenant}:{project}:{bundle_id}"
        PRELOAD_BUNDLE_DONE_FMT = "kdcube:config:bundles:preload-done:{tenant}:{project}:{bundle_id}:{generation}"
        DEPROVISION_LOCK_FMT = "kdcube:config:bundles:deprovision-lock:{tenant}:{project}:{bundle_id}"
        DEPROVISION_RESULT_FMT = "kdcube:config:bundles:deprovision-result:{tenant}:{project}:{bundle_id}:{operation_id}"

    class GATEWAY:
        NAMESPACE = "kdcube:config:gateway"
        UPDATE_CHANNEL = "kdcube:config:gateway:update"
        CURRENT_KEY = "current"

    class ECONOMICS:
        SYNC_CHANNEL = "kdcube:config:economics:sync:{tenant}:{project}"
