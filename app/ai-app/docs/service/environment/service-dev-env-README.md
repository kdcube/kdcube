---
id: ks:docs/service/environment/service-dev-env-README.md
title: "Service Dev Env"
summary: "Local IDE/CLI runs of ingress/proc/metrics with external infra."
tags: ["service", "environment", "dev", "local"]
keywords: ["local dev", "ingress", "proc", "metrics", "local-infra-stack"]
see_also:
  - ks:docs/service/environment/service-compose-env-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/service-ecs-env-README.md
---
# Service Dev Env (Local Run)

This document covers **local IDE/CLI runs** of `chat-ingress`, `chat-proc`, and `metrics`
while infra (Postgres/Redis/ClamAV/proxylogin) runs separately (for example via
`deployment/docker/local-infra-stack` or managed services).

## Source‑Of‑Truth Env Files

Use the sample envs below as the authoritative list and copy into your IDE run config.
You can also use `kdcube-setup` (PyPI package: `kdcube-cli`) to generate a workdir and then point your IDE
to the generated env files in `workdir/config`.

| File | Purpose |
| --- | --- |
| `deployment/devenv/sample_env/.env.ingress` | Ingress service (SSE + REST) |
| `deployment/devenv/sample_env/.env.proc` | Processor service (queue + bundles + integrations) |
| `deployment/devenv/sample_env/.env.metrics` | Metrics service (Redis aggregation + export) |
| `deployment/devenv/sample_env/.env.frontend` | Frontend dev build (optional) |
| `deployment/devenv/sample_env/.env.postgres.setup` | One‑shot DB setup job (optional) |

**Important:** `tenant` + `project` are now **only** read from `GATEWAY_CONFIG_JSON`.
Do not use `TENANT_ID`, `PROJECT_ID`, or `DEFAULT_PROJECT_NAME`.
For CI/CD or reproducible restarts, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1`
to overwrite cached gateway config from env on each start.

## Shared Core (Ingress + Proc + Metrics)

| Variable | Purpose |
| --- | --- |
| `GATEWAY_CONFIG_JSON` | Shared gateway config (tenant+project required). See sample JSON in `.env.ingress`/`.env.proc`/`.env.metrics`. |
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | If `1`, overwrite cached gateway config from env on startup (CI/CD). |
| `GATEWAY_COMPONENT` | Component identity: `ingress`, `proc`, or `metrics`. |
| `POSTGRES_HOST` | Postgres host |
| `POSTGRES_PORT` | Postgres port |
| `POSTGRES_DATABASE` | Database name |
| `POSTGRES_USER` | Database user |
| `POSTGRES_PASSWORD` | Database password |
| `POSTGRES_SSL` | SSL mode for Postgres |
| `REDIS_HOST` | Redis host |
| `REDIS_PASSWORD` | Redis password |
| `REDIS_URL` | Full Redis URL (use in code) |
| `KDCUBE_STORAGE_PATH` | Storage path (`file:///...` or `s3://...`) |
| `LOG_LEVEL` | Log level |
| `HEARTBEAT_INTERVAL` | Heartbeat interval (seconds) |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | AWS region (S3/CloudWatch) |
| `AWS_SDK_LOAD_CONFIG` | Allow AWS SDK to read `~/.aws/config` |
| `NO_PROXY`, `AWS_EC2_METADATA_DISABLED` | Only needed when running on EC2 or in docker‑compose on EC2 |

## Ingress‑Only (`chat-ingress`)

| Variable | Purpose |
| --- | --- |
| `CHAT_APP_PORT` | Ingress port (default `8010`) |
| `CORS_CONFIG` | CORS config JSON |
| `AUTH_PROVIDER` | `simple` or `cognito` |
| `ID_TOKEN_HEADER_NAME` | Extra ID token header (non‑simple auth) |
| `STREAM_ID_HEADER_NAME` | Header carrying connected peer/stream id for REST → SSE/socket targeting |
| `AUTH_TOKEN_COOKIE_NAME` / `ID_TOKEN_COOKIE_NAME` | Cookie names (proxylogin) |
| `COGNITO_*` | Cognito settings if used |
| `OIDC_SERVICE_ADMIN_*` | Service account for auth |
| `APP_AV_SCAN` / `APP_AV_TIMEOUT_S` / `CLAMAV_HOST` / `CLAMAV_PORT` | AV scan settings |
| `OPEX_AGG_CRON` | Accounting aggregation schedule |
| `STRIPE_RECONCILE_ENABLED` | Enable/disable Stripe reconcile job (default `true`) |
| `STRIPE_RECONCILE_CRON` | Stripe reconcile schedule (default `45 * * * *`) |
| `STRIPE_RECONCILE_LOCK_TTL_SECONDS` | Distributed lock TTL for reconcile job (default `900`) |
| `SUBSCRIPTION_ROLLOVER_ENABLED` | Enable/disable subscription rollover job (default `true`) |
| `SUBSCRIPTION_ROLLOVER_CRON` | Subscription rollover schedule (default `15 * * * *`) |
| `SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS` | Distributed lock TTL for rollover job (default `900`) |
| `SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT` | Max subscriptions processed per rollover run (default `500`) |

## Proc‑Only (`chat-proc`)

| Variable | Purpose |
| --- | --- |
| `CHAT_PROCESSOR_PORT` | Proc port (default `8020`) |
| `CHAT_TASK_TIMEOUT_SEC` | Per‑task timeout |
| `AGENTIC_BUNDLES_JSON` | Bundles registry JSON (runtime descriptor) |
| `EXEC_WORKSPACE_ROOT` | Host workspace for code execution |
| `PY_CODE_EXEC_*` | Code exec image + timeout + network mode |
| `BUNDLE_CLEANUP_*` | Bundle cleanup loop settings |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | Force registry overwrite from env on startup |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | Enable/disable git bundle resolution |
| `BUNDLE_GIT_*` | Git clone behavior (atomic, shallow, keep/ttl, locks, always‑pull) |
| `GIT_SSH_*` / `GIT_SSH_COMMAND` | SSH auth for private git repos |
| `TOOLS_WEB_SEARCH_FETCH_CONTENT`, `WEB_*`, `MCP_CACHE_TTL_SECONDS` | Web fetch/search tooling |
| `REACT_PERSIST_WORKSPACE` | Persist per‑turn workspace snapshot (diagnostic only) |

## Metrics‑Only

| Variable | Purpose |
| --- | --- |
| `METRICS_PORT` | Metrics service port |
| `METRICS_MODE` | `redis` (default) |
| `METRICS_SCHEDULER_ENABLED` | Run scheduler loop |
| `METRICS_EXPORT_INTERVAL_SEC` | Export cadence |
| `METRICS_EXPORT_CLOUDWATCH` | CloudWatch export toggle |
| `METRICS_CLOUDWATCH_NAMESPACE` | CloudWatch namespace |
| `METRICS_CLOUDWATCH_REGION` | CloudWatch region override |
| `METRICS_EXPORT_PROMETHEUS_PUSH` | Pushgateway export toggle |
| `METRICS_PROM_PUSHGATEWAY_URL` | Pushgateway URL |

## Frontend (optional)

| Variable | Purpose |
| --- | --- |
| `CHAT_WEB_APP_CONFIG_FILE_PATH` | UI config JSON path |

## Minimal Gateway Config Example

See `deployment/devenv/sample_env/.env.ingress` (or `.env.proc`) for a full, readable example.
Minimal skeleton:

```json
{
  "tenant": "<TENANT_ID>",
  "project": "<PROJECT_ID>",
  "profile": "development",
  "service_capacity": {
    "ingress": { "processes_per_instance": 1 },
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

For the full schema, see [docs/service/gateway-README.md](../gateway-README.md).
