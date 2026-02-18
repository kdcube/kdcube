# Service Configuration — Chat Platform

This document summarizes **runtime configuration** for the chat service.  
It focuses on tenant/project/bundle settings, instance identity, and parallelism.

**Sample env files**

- Compose/env wiring: `deployment/docker/all_in_one/sample_env/.env`
- Service runtime env: `deployment/docker/all_in_one/sample_env/.env.backend`

**Short pitch (capacity + limits)**  
The chat service is **rate‑limited and capacity‑limited** by design:

- Requests are admitted only if the system has capacity (gateway + queue backpressure).
- Excess load is **rejected early** with `queue.enqueue_rejected`.
- Concurrency limits keep each processor stable under load.

For gateway‑level rate limits and backpressure configuration, see `docs/gateway-README.md`.
## Tenant / Project / Bundles

These values scope **Redis keys**, **bundle registries**, and **control‑plane events**.

| Setting | Default | Purpose | Used by |
| --- | --- | --- | --- |
| `TENANT_ID` | `home` | Tenant scope for chat service and bundle registry | `apps/chat/sdk/config.py`, bundle registry/store |
| `DEFAULT_PROJECT_NAME` | `default-project` | Project scope for chat service and bundle registry | `apps/chat/sdk/config.py`, bundle registry/store |
| `AGENTIC_BUNDLES_JSON` | _(unset)_ | Seed bundle registry from JSON | `infra/plugin/bundle_store.py` |
| `HOST_BUNDLES_PATH` | _(unset)_ | Host path for bundle roots (git‑cloned or manually provisioned). Often mounted into containers. | `infra/plugin/git_bundle.py` |
| `AGENTIC_BUNDLES_ROOT` | _(unset)_ | Container‑visible bundles root (path used by runtime inside container). | `infra/plugin/git_bundle.py` |
| `BUNDLE_GIT_ALWAYS_PULL` | `0` | Force refresh on resolve | `infra/plugin/bundle_registry.py` |
| `BUNDLE_GIT_ATOMIC` | `1` | Atomic clone/update | `infra/plugin/git_bundle.py` |
| `BUNDLE_GIT_SHALLOW` | `1` | Shallow clone mode | `infra/plugin/git_bundle.py` |
| `BUNDLE_GIT_CLONE_DEPTH` | `50` | Shallow clone depth | `infra/plugin/git_bundle.py` |
| `BUNDLE_GIT_KEEP` | `3` | Keep N old bundle dirs | `infra/plugin/git_bundle.py` |
| `BUNDLE_GIT_TTL_HOURS` | `0` | TTL cleanup for old bundle dirs | `infra/plugin/git_bundle.py` |
| `BUNDLE_REF_TTL_SECONDS` | `3600` | TTL for active bundle refs | `infra/plugin/bundle_refs.py` |

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

| Setting                 | Default | Purpose | Used by                                                       |
|-------------------------|---------| --- |---------------------------------------------------------------|
| `CHAT_APP_PARALLELISM`  | `1`     | Number of worker processes per instance | `infra/gateway/config.py`, monitoring/heartbeat expectations  |
| `MAX_CONCURRENT_CHAT`   | `5`     | Max concurrent chat tasks per processor | `apps/chat/processor.py`                                      |
| `CHAT_TASK_TIMEOUT_SEC` | `600`   | Per‑task timeout (seconds) | `apps/chat/processor.py`                                      |
| `MAX_QUEUE_SIZE`        | `0`     | Hard queue size limit (0 = disabled) | `infra/gateway/backpressure.py`                               |

**Note:** The following are currently **not enforced** in the chat service (present only in examples):

- `ORCHESTRATOR_WORKER_CONCURRENCY`

If/when these are wired into runtime limits, this doc should be updated.

## Queue Rejection (Backpressure)

When queue capacity is exceeded, the enqueue step rejects the request and
returns a **system error** with:

- `error_type`: `queue.enqueue_rejected`
- `http_status`: `503`
- `reason`: one of
  - `queue_size_exceeded` (when `MAX_QUEUE_SIZE` is set and exceeded)
  - `hard_limit_exceeded` / `registered_threshold_exceeded` / `anonymous_threshold_exceeded`

This is emitted from `apps/chat/api/ingress/chat_core.py` and handled in SSE at
`apps/chat/api/sse/chat.py`.

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

**Control plane schema**

Deploy the economics schema before enabling control plane endpoints:

- [deploy-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

**Stripe configuration**

| Setting | Default | Purpose |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | _(unset)_ | Stripe API key (preferred) |
| `STRIPE_API_KEY` | _(unset)_ | Fallback Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | _(unset)_ | Webhook signature verification |

If `STRIPE_WEBHOOK_SECRET` is not set, webhook payloads are accepted without signature verification (not recommended).

**Admin email notifications**

| Setting | Default | Purpose |
| --- | --- | --- |
| `EMAIL_ENABLED` | `true` | Enable admin email notifications |
| `EMAIL_HOST` | _(unset)_ | SMTP host |
| `EMAIL_PORT` | `587` | SMTP port |
| `EMAIL_USER` | _(unset)_ | SMTP username |
| `EMAIL_PASSWORD` | _(unset)_ | SMTP password |
| `EMAIL_FROM` | _(EMAIL_USER)_ | From address |
| `EMAIL_TO` | `lena@nestlogic.com` | Default recipient |
| `EMAIL_USE_TLS` | `true` | Enable TLS |

Admin emails are sent for wallet refunds and subscription cancels/reconciles.
