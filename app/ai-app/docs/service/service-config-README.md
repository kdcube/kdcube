# Service Configuration — Chat Platform

This document summarizes **runtime configuration** for the chat service.  
It focuses on tenant/project/bundle settings, instance identity, and parallelism.

**Sample env files (per service)**

- Ingress: `deployment/docker/devenv/sample_env/.env.ingress`
- Proc: `deployment/docker/devenv/sample_env/.env.proc`
- Metrics: `deployment/docker/devenv/sample_env/.env.metrics`

**Short pitch (capacity + limits)**  
The chat service is **rate‑limited and capacity‑limited** by design:

- Requests are admitted only if the system has capacity (gateway + queue backpressure).
- Excess load is **rejected early** with `queue.enqueue_rejected`.
- Concurrency limits keep each processor stable under load.

For gateway‑level rate limits and backpressure configuration, see `docs/gateway-README.md`.
## Gateway Config (Required)

Tenant/project **must** be provided via `GATEWAY_CONFIG_JSON` (per tenant/project).
There are no supported env fallbacks for tenant/project anymore.

### Top-level keys (required unless noted)

| Key                     | Required | Purpose                                                           |
|-------------------------|----------|-------------------------------------------------------------------|
| `tenant`                | ✅        | Tenant scope for Redis keys, bundles, and control‑plane events    |
| `project`               | ✅        | Project scope for Redis keys, bundles, and control‑plane events   |
| `profile`               | ➖        | `development` / `production` (defaults to development)            |
| `guarded_rest_patterns` | ➖        | Regexes for protected REST routes                                 |

### Component-aware sections

Each section can be **flat** or **component‑scoped** (`ingress`, `proc`).  
When component‑scoped, each service reads its own subsection based on `GATEWAY_COMPONENT`.

| Section              | Keys (examples)                                                                            | Purpose                                                     |
|----------------------|--------------------------------------------------------------------------------------------|-------------------------------------------------------------|
| `service_capacity`   | `processes_per_instance`, `concurrent_requests_per_process`, `avg_processing_time_seconds` | Capacity sizing. Used for backpressure math and validation. |
| `backpressure`       | `capacity_buffer`, `queue_depth_multiplier`, thresholds, `capacity_source_component`       | Queue/backpressure settings and capacity source selector.   |
| `rate_limits`        | role limits (`hourly`, `burst`, `burst_window`)                                            | Per‑role rate limiting (per session).                       |
| `pools`              | `pg_pool_min_size`, `pg_pool_max_size`, `redis_max_connections`, `pg_max_connections`      | Pool sizing per component; optional DB max for warnings.    |
| `limits`             | `max_sse_connections_per_instance`, `max_integrations_ops_concurrency`, `max_queue_size`   | Soft limits for ingress/proc.                               |
| `redis`              | `sse_stats_ttl_seconds`, `sse_stats_max_age_seconds`                                       | Redis‑based SSE stats retention.                            |

### Example (readable, component‑scoped)

```json
{
  "tenant": "tenant-id",
  "project": "project-id",
  "profile": "development",
  "service_capacity": {
    "ingress": {
      "processes_per_instance": 2
    },
    "proc": {
      "concurrent_requests_per_process": 8,
      "processes_per_instance": 4,
      "avg_processing_time_seconds": 25
    }
  },
  "backpressure": {
    "capacity_source_component": "proc",
    "ingress": {
      "capacity_buffer": 0.1,
      "queue_depth_multiplier": 3,
      "anonymous_pressure_threshold": 0.6,
      "registered_pressure_threshold": 0.85,
      "paid_pressure_threshold": 0.9,
      "hard_limit_threshold": 0.98
    },
    "proc": {
      "capacity_buffer": 0.1,
      "queue_depth_multiplier": 3,
      "anonymous_pressure_threshold": 0.6,
      "registered_pressure_threshold": 0.85,
      "paid_pressure_threshold": 0.9,
      "hard_limit_threshold": 0.98
    }
  },
  "rate_limits": {
    "ingress": {
      "anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
      "registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
      "paid": { "hourly": 4000, "burst": 150, "burst_window": 60 },
      "privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
    },
    "proc": {
      "anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
      "registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
      "paid": { "hourly": 4000, "burst": 150, "burst_window": 60 },
      "privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
    }
  },
  "pools": {
    "ingress": { "pg_pool_min_size": 0, "pg_pool_max_size": 4, "redis_max_connections": 20 },
    "proc": { "pg_pool_min_size": 0, "pg_pool_max_size": 8, "redis_max_connections": 40 },
    "pg_max_connections": 100
  },
  "limits": {
    "ingress": { "max_sse_connections_per_instance": 200 },
    "proc": { "max_integrations_ops_concurrency": 200, "max_queue_size": 100 }
  },
  "redis": {
    "sse_stats_ttl_seconds": 60,
    "sse_stats_max_age_seconds": 120
  }
}
```

## Core Connectivity (All Services)

### Postgres

| Setting              | Required | Purpose |
|----------------------|----------|---------|
| `POSTGRES_HOST`      | ✅        | Hostname of Postgres (managed or local) |
| `POSTGRES_PORT`      | ✅        | Port (default 5432) |
| `POSTGRES_DATABASE`  | ✅        | DB name |
| `POSTGRES_USER`      | ✅        | DB user |
| `POSTGRES_PASSWORD`  | ✅        | DB password |
| `POSTGRES_SSL`       | ➖        | `True` if managed DB requires SSL |

### Redis

| Setting            | Required | Purpose |
|--------------------|----------|---------|
| `REDIS_URL`        | ✅        | Full Redis connection URL |
| `REDIS_HOST`       | ➖        | Host, used to build `REDIS_URL` if not provided |
| `REDIS_PORT`       | ➖        | Port (default 6379) |
| `REDIS_PASSWORD`   | ➖        | Password (if required) |
| `REDIS_DB`         | ➖        | DB index (default 0) |

### Storage

| Setting                 | Required | Purpose |
|-------------------------|----------|---------|
| `KDCUBE_STORAGE_PATH`   | ✅        | Storage root (local FS `file:///...` or `s3://bucket/path`) |
| `CB_BUNDLE_STORAGE_URL` | ➖        | Bundle storage URL (proc only; defaults to storage path) |

### ClamAV (Ingress only)

| Setting             | Required | Purpose |
|---------------------|----------|---------|
| `APP_AV_SCAN`       | ➖        | Enable AV scanning (1/0) |
| `APP_AV_TIMEOUT_S`  | ➖        | Scan timeout per file |
| `CLAMAV_HOST`       | ➖        | ClamAV host |
| `CLAMAV_PORT`       | ➖        | ClamAV port |

### Auth

| Setting                     | Required | Purpose |
|-----------------------------|----------|---------|
| `AUTH_PROVIDER`             | ✅        | `simple` or `cognito` |
| `ID_TOKEN_HEADER_NAME`      | ➖        | Header for id token |
| `AUTH_TOKEN_COOKIE_NAME`    | ➖        | Access token cookie name |
| `ID_TOKEN_COOKIE_NAME`      | ➖        | ID token cookie name |
| `COGNITO_REGION`            | ➖        | Cognito region |
| `COGNITO_USER_POOL_ID`      | ➖        | Cognito user pool |
| `COGNITO_APP_CLIENT_ID`     | ➖        | Cognito client id |
| `COGNITO_SERVICE_CLIENT_ID` | ➖        | Service client id |

### CORS

| Setting        | Required | Purpose |
|----------------|----------|---------|
| `CORS_CONFIG`  | ➖        | JSON config for allowed origins/headers/methods |

### Model Provider Keys (optional)

| Setting                 | Purpose |
|-------------------------|---------|
| `OPENAI_API_KEY`        | OpenAI access |
| `ANTHROPIC_API_KEY`     | Anthropic access |
| `GEMINI_API_KEY`        | Google Gemini access |
| `HUGGING_FACE_API_TOKEN`| Hugging Face access |
| `BRAVE_API_KEY`         | Brave search |

### Exec (Processor only)

| Setting                | Purpose |
|------------------------|---------|
| `PY_CODE_EXEC_IMAGE`   | Exec runtime image |
| `PY_CODE_EXEC_TIMEOUT` | Exec timeout (seconds) |
| `PY_CODE_EXEC_NETWORK_MODE` | Docker network mode |
| `EXEC_WORKSPACE_ROOT`  | Local workspace path |

## Bundles

These values scope **bundle registries** and **control‑plane events**.

| Setting                  | Default   | Purpose                                                                                         | Used by                           |
|--------------------------|-----------|-------------------------------------------------------------------------------------------------|-----------------------------------|
| `AGENTIC_BUNDLES_JSON`   | _(unset)_ | Seed bundle registry from JSON                                                                  | `infra/plugin/bundle_store.py`    |
| `HOST_BUNDLES_PATH`      | _(unset)_ | Host path for bundle roots (git‑cloned or manually provisioned). Often mounted into containers. | `infra/plugin/git_bundle.py`      |
| `AGENTIC_BUNDLES_ROOT`   | _(unset)_ | Container‑visible bundles root (path used by runtime inside container).                         | `infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_ALWAYS_PULL` | `0`       | Force refresh on resolve                                                                        | `infra/plugin/bundle_registry.py` |
| `BUNDLE_GIT_ATOMIC`      | `1`       | Atomic clone/update                                                                             | `infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_SHALLOW`     | `1`       | Shallow clone mode                                                                              | `infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_CLONE_DEPTH` | `50`      | Shallow clone depth                                                                             | `infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_KEEP`        | `3`       | Keep N old bundle dirs                                                                          | `infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_TTL_HOURS`   | `0`       | TTL cleanup for old bundle dirs                                                                 | `infra/plugin/git_bundle.py`      |
| `BUNDLE_REF_TTL_SECONDS` | `3600`    | TTL for active bundle refs                                                                      | `infra/plugin/bundle_refs.py`     |

**Tenant/project scoped channels**

Control‑plane updates and cleanup are published to:

```
kdcube:config:bundles:update:{tenant}:{project}
kdcube:config:bundles:cleanup:{tenant}:{project}
```

Each processor instance only subscribes to its own tenant/project channel.

**Host vs container bundles root**

- `HOST_BUNDLES_PATH` is typically defined in `.env` (docker‑compose) so the host path can be mounted.
- `AGENTIC_BUNDLES_ROOT` is typically defined in `.env.backend` so the service inside the container knows the path.

**Admin bundle**

The built‑in admin bundle (`kdcube.admin`) lives inside the SDK and is used to serve admin UIs.
It is always present in the registry (auto‑injected if missing). Later it can also provide
product‑level chatbot capabilities.

## Instance Identity

| Setting                | Default           | Purpose | Used by                                                                                                             |
|------------------------|-------------------| --- |---------------------------------------------------------------------------------------------------------------------|
| `INSTANCE_ID`          | `home-instance-1` | Instance identity for heartbeats & monitoring | `apps/chat/sdk/config.py`, `infra/availability/health_and_heartbeat.py`                                             |
| `HEARTBEAT_INTERVAL`   | `10`              | Heartbeat interval (seconds) | Orchestrator + KB services (`infra/orchestration/app/dramatiq/resolver.py`, `apps/knowledge_base/api/resolvers.py`) |

## Parallelism / Capacity

| Setting                                                                     | Default | Purpose                                | Used by                                           |
|-----------------------------------------------------------------------------|---------|----------------------------------------|---------------------------------------------------|
| `GATEWAY_CONFIG_JSON.service_capacity.proc.processes_per_instance`          | `1`     | Proc worker processes per instance     | `infra/gateway/config.py`, heartbeat expectations |
| `GATEWAY_CONFIG_JSON.service_capacity.proc.concurrent_requests_per_process` | `5`     | Max concurrent chat tasks per proc     | `apps/chat/processor.py`                          |
| `GATEWAY_CONFIG_JSON.service_capacity.proc.avg_processing_time_seconds`     | `25`    | Capacity math / throughput estimate    | `infra/gateway/config.py`                         |
| `GATEWAY_CONFIG_JSON.service_capacity.ingress.processes_per_instance`       | `1`     | Ingress worker processes per instance  | `infra/gateway/config.py`                         |
| `GATEWAY_CONFIG_JSON.limits.proc.max_queue_size`                            | `0`     | Hard queue size limit (0 = disabled)   | `infra/gateway/backpressure.py`                   |
| `CHAT_TASK_TIMEOUT_SEC`                                                     | `600`   | Per‑task timeout (seconds)             | `apps/chat/processor.py`                          |

**Note:** The following are currently **not enforced** in the chat service (present only in examples):

- `ORCHESTRATOR_WORKER_CONCURRENCY`

If/when these are wired into runtime limits, this doc should be updated.

## Queue Rejection (Backpressure)

When queue capacity is exceeded, the enqueue step rejects the request and
returns a **system error** with:

- `error_type`: `queue.enqueue_rejected`
- `http_status`: `503`
- `reason`: one of
  - `queue_size_exceeded` (when `GATEWAY_CONFIG_JSON.limits.proc.max_queue_size` is set and exceeded)
  - `hard_limit_exceeded` / `registered_threshold_exceeded` / `anonymous_threshold_exceeded`

This is emitted from `apps/chat/api/ingress/chat_core.py` and handled in SSE at
`apps/chat/api/sse/chat.py`.

## Metrics & Rolling Windows

The monitoring pipeline stores **rolling metrics** in Redis (tenant/project‑scoped):

- SSE connections (1m/15m/1h/max)
- Queue depth + pressure (1m/15m/1h/max)
- Pool utilization + max in‑use (1m/15m/1h/max)
- Task latency percentiles (queue wait + exec p50/p95/p99)
- Ingress REST latency percentiles (p50/p95/p99)

Retention is **1 hour**. Metrics are exposed via:
`GET /monitoring/system` and the Metrics server (`docs/service/scale/metric-server-README.md`).

## Scheduling (OPEX + Bundle Cleanup)

These settings are now **first‑class** in `Settings` (`apps/chat/sdk/config.py`).

| Setting                           | Default     | Purpose                                   |
|-----------------------------------|-------------|-------------------------------------------|
| `OPEX_AGG_CRON`                   | `0 3 * * *` | Schedule for daily accounting aggregation |
| `BUNDLE_CLEANUP_ENABLED`          | `true`      | Enable periodic git bundle cleanup        |
| `BUNDLE_CLEANUP_INTERVAL_SECONDS` | `3600`      | Cleanup interval                          |
| `BUNDLE_CLEANUP_LOCK_TTL_SECONDS` | `900`       | Redis lock TTL for cleanup loop           |

The cleanup loop uses Redis locks to avoid multi‑worker collisions.

## Economics

Economics requires PostgreSQL (control‑plane schema) and Redis (rate limiting + analytics).
Stripe and email are optional but recommended for production.

Control plane schema

Deploy the economics schema before enabling control plane endpoints:

- [deploy-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

Plan quota seeding

- A master bundle seeds `plan_quota_policies` from `app_quota_policies` on first run.
- After seeding, update limits in the admin UI (Quota Policies card).
- If code defaults change, update DB policies or clear the table to re‑seed.

Stripe configuration

| Setting                 | Default | Purpose                        |
|-------------------------| --- |--------------------------------|
| `STRIPE_SECRET_KEY`     | _(unset)_ | Stripe API key (preferred)     |
| `STRIPE_API_KEY`        | _(unset)_ | Fallback Stripe API key        |
| `STRIPE_WEBHOOK_SECRET` | _(unset)_ | Webhook signature verification |

If `STRIPE_WEBHOOK_SECRET` is not set, webhook payloads are accepted without signature verification (not recommended).

Admin email notifications

| Setting          | Default              | Purpose                           |
|------------------|----------------------|-----------------------------------|
| `EMAIL_ENABLED`  | `true`               | Enable admin email notifications  |
| `EMAIL_HOST`     | _(unset)_            | SMTP host                         |
| `EMAIL_PORT`     | `587`                | SMTP port                         |
| `EMAIL_USER`     | _(unset)_            | SMTP username                     |
| `EMAIL_PASSWORD` | _(unset)_            | SMTP password                     |
| `EMAIL_FROM`     | _(EMAIL_USER)_       | From address                      |
| `EMAIL_TO`       | `ops@example.com`    | Default recipient                 |
| `EMAIL_USE_TLS`  | `true`               | Enable TLS                        |

Admin emails are sent for wallet refunds and subscription cancels/reconciles.
