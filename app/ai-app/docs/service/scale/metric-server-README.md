---
id: ks:docs/service/scale/metric-server-README.md
title: "Metric Server"
summary: "Metrics server architecture and exports for autoscaling."
tags: ["service", "metrics", "autoscaling", "observability"]
keywords: ["metrics server", "CloudWatch", "Prometheus", "redis metrics", "autoscaler"]
see_also:
  - ks:docs/service/scale/metrics-README.md
  - ks:docs/service/README-monitoring-observability.md
  - ks:docs/ops/ops-overview-README.md
  - ks:docs/service/ecs/custom-ecs-README.md
---
# Metrics Server (Autoscaling Source of Truth)

This document explains **what the Metrics server is**, how it collects data,
and how it integrates with autoscalers (ECS/CloudWatch or Prometheus/HPA).

---

## 1) What the Metrics server does

The Metrics server is a **single internal service** that aggregates system
signals and **exports** them for autoscaling.

It can operate in two modes:

### A) **Redis mode** (default, recommended)
- **No REST calls** to other services.
- Reads all required stats directly from Redis.
- Avoids Cognito tokens entirely.
- Works well in private networks and is safe for production.

In Redis mode it also computes **rolling windows** and **percentiles**:
- SSE connections (1m/15m/1h/max)
- Queue depth + pressure (1m/15m/1h/max)
- Pool utilization + max in‑use (1m/15m/1h/max)
- Task latency percentiles (p50/p95/p99 for 1m/15m/1h)
- Ingress REST latency percentiles (p50/p95/p99 for 1m/15m/1h)

Important distinction:
- The **system endpoints** (`/metrics/ingress/system`, `/metrics/proc/system`, `/metrics/combined`) expose the full monitoring payload from `compute_system_monitoring(...)`.
- The **export path** (`/metrics`, CloudWatch export, Pushgateway export) currently publishes a smaller scalar subset derived by `_extract_metrics(...)`.

Current exported scalar keys:
- `ingress.sse.total_connections`
- `ingress.sse.max_connections`
- `ingress.sse.sessions`
- `ingress.sse.saturation_percent`
- `ingress.sse.saturation_ratio`
- `proc.queue.total`
- `proc.queue.utilization_percent`
- `proc.queue.utilization_ratio`
- `proc.pressure_ratio_percent`
- `proc.queue.pressure_ratio`
- `proc.queue.wait.p95_ms`
- `proc.exec.p95_ms`
- `proc.queue.depth.1m`
- `proc.queue.pressure_ratio.1m`
- `proc.queue.avg_wait_seconds.anonymous`
- `proc.queue.avg_wait_seconds.registered`
- `proc.queue.avg_wait_seconds.paid`
- `proc.queue.avg_wait_seconds.privileged`
- `throttling.rate_limit_429`
- `throttling.backpressure_503`

Raw proc watchdog note:
- proc heartbeat/debug fields such as `processor.task_idle_timeout_sec`,
  `processor.task_max_wall_time_sec`, `processor.oldest_active_task_wall_age_sec`,
  and `processor.max_active_task_idle_age_sec` are **not** part of the exported scalar set
- they remain raw processor observability data in Redis heartbeats and admin monitoring views

Proc latency export note:
- the Metrics server exports the proc latency metrics from the **`1m` percentile window** produced by `compute_system_monitoring(...)`
- specifically:
  - `proc.queue.wait.p95_ms` comes from `components.proc.latency.queue_wait_ms.1m.p95`
  - `proc.exec.p95_ms` comes from `components.proc.latency.exec_ms.1m.p95`
- older flat shapes such as `components.proc.latency.queue_wait_ms.p95` are only treated as a compatibility fallback

### B) **Proxy mode** (optional)
- Calls configured upstream `/monitoring/system` endpoints.
- Requires auth headers or tokens.
- Useful in dev or when you want *exactly* what those endpoints return.

Current limitation:
- The chat ingress/control-plane service exposes `/monitoring/system`.
- The current proc FastAPI app does **not** mount that route directly.
- In practice, proxy mode is mainly useful for ingress monitoring unless you point
  `METRICS_PROC_BASE_URL` at a service that already exposes the aggregated monitoring endpoint.

---

## 2) Why Redis mode is preferred

You asked to avoid static auth tokens (especially with Cognito).
Redis mode achieves this because:

- It does **not** call any protected HTTP endpoints.
- It only needs **Redis access** (and optionally Postgres).
- It can run in a private subnet.

If you need **Prometheus** or **CloudWatch** integration, Redis mode still works.

---

## 3) How autoscalers consume metrics

### ECS (CloudWatch)
1. Metrics server **pushes metrics** to CloudWatch (`METRICS_EXPORT_CLOUDWATCH=1`).
2. ECS Service Auto Scaling uses those metrics (Target Tracking or Step Scaling).

### Kubernetes (Prometheus + HPA)
1. Prometheus scrapes the **Metrics server** at `/metrics`.
2. Prometheus Adapter exposes metrics as HPA signals.

### Other vendors
Same pattern: choose either **push** (CloudWatch/Pushgateway) or **pull** (`/metrics`).

---

## 4) Environment variables (full reference)

## 4.1) Current configuration source of truth

The Metrics service now reads its own runtime/export settings through
`get_settings()` from the descriptor-backed settings layer.

Current runtime contract:
- `assembly.yaml`
  - `ports.metrics`
  - `platform.services.metrics.log.*`
  - `platform.services.metrics.service.*`
  - `platform.services.metrics.proxy.*`
  - `platform.services.metrics.export.*`
  - shared AWS settings such as `aws.aws_region`
- `gateway.yaml`
  - tenant/project
  - service-capacity, backpressure, rate-limit, pool, and Redis monitoring settings

Important implementation detail:
- the Metrics service still sets `GATEWAY_COMPONENT=proc`
- that is intentional and preserves the existing processor-capacity semantics used
  by queue/backpressure calculations
- metrics-specific settings are therefore read from the explicit
  `platform.services.metrics.*` path, not from the generic component-scoped
  `platform.services.<component>.*` path

Supported descriptor paths:
- `ports.metrics`
- `platform.services.metrics.log.log_level`
- `platform.services.metrics.log.log_max_mb`
- `platform.services.metrics.log.log_backup_count`
- `platform.services.metrics.log.log_dir`
- `platform.services.metrics.log.log_file_prefix`
- `platform.services.metrics.service.metrics_mode`
- `platform.services.metrics.service.metrics_request_timeout_sec`
- `platform.services.metrics.service.metrics_enable_pg_pool`
- `platform.services.metrics.proxy.metrics_ingress_base_url`
- `platform.services.metrics.proxy.metrics_proc_base_url`
- `platform.services.metrics.proxy.metrics_auth_header_name`
- `platform.services.metrics.proxy.metrics_auth_header_value`
- `platform.services.metrics.proxy.metrics_headers_json`
- `platform.services.metrics.export.scheduler_enabled`
- `platform.services.metrics.export.export_interval_sec`
- `platform.services.metrics.export.export_on_start`
- `platform.services.metrics.export.run_once`
- `platform.services.metrics.export.mapping_json`
- `platform.services.metrics.export.cloudwatch.enabled`
- `platform.services.metrics.export.cloudwatch.namespace`
- `platform.services.metrics.export.cloudwatch.region`
- `platform.services.metrics.export.cloudwatch.dimensions_json`
- `platform.services.metrics.export.prometheus.push_enabled`
- `platform.services.metrics.export.prometheus.pushgateway_url`
- `platform.services.metrics.export.prometheus.job_name`
- `platform.services.metrics.export.prometheus.grouping_labels_json`
- `platform.services.metrics.export.prometheus.scrape_ttl_sec`

Backward-compatibility note:
- env vars with the existing `METRICS_*` names still work as fallbacks
- descriptor values are the preferred source when mounted

About `src/.../apps/metrics/.env.metrics`:
- that file remains a **local/sample env source** for direct local runs
- it is no longer the primary configuration contract for descriptor-driven deployments

### Core
| Env | Default | Meaning |
|---|---|---|
| `METRICS_MODE` | `redis` | `redis` (direct) or `proxy` (calls configured `/monitoring/system` upstreams). |
| `METRICS_PORT` | `8090` | Metrics server port. |
| `METRICS_ENABLE_PG_POOL` | `0` | If `1`, create Postgres pool to query `max_connections`. |
| `GATEWAY_CONFIG_JSON` | — | Optional highest-precedence gateway source. If absent, metrics falls back to `GATEWAY_YAML_PATH` or `PLATFORM_DESCRIPTORS_DIR/gateway.yaml`. |
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | `0` | If `1`, overwrite cached gateway config from env on startup. |

### Redis mode
Redis mode uses standard app env:
- `REDIS_URL`, `INSTANCE_ID`, plus a gateway source that provides `tenant` and `project`:
  - `GATEWAY_CONFIG_JSON`
  - or `GATEWAY_YAML_PATH`
  - or `PLATFORM_DESCRIPTORS_DIR/gateway.yaml`
Optional SSE stats tuning (gateway config):
- `redis.sse_stats_ttl_seconds` (default `60`) – TTL for per‑process SSE stats.
- `redis.sse_stats_max_age_seconds` (default `120`) – ignore older samples.

No auth tokens required.

The metrics server operates **per tenant/project**.  
Run one metrics instance per tenant/project by supplying gateway config with the
target `tenant`/`project`.

Note:
- The metrics server sets `GATEWAY_COMPONENT=proc` by default so queue/backpressure
  calculations use **processor** capacity.

Redis mode now includes SSE counts:
- Each ingress worker publishes SSE counts to Redis under
  `kdcube:chat:sse:connections:<instance>:<pid>` (namespaced by tenant/project).
- The metrics server aggregates these keys and returns `sse_connections` in Redis mode.
- TTL is controlled by gateway config `redis.sse_stats_ttl_seconds` (default `60`).
- Rolling windows are computed by the Metrics server and stored under
  `kdcube:metrics:*` keys (tenant/project‑scoped).

Operational note:
- The metrics service itself still initializes the legacy shared Redis client bundle
  (`redis_async`, `redis_async_decode`, `redis_sync`) in its own process.
- `GATEWAY_COMPONENT=proc` selects the **proc config slice** for capacity math; it does
  **not** change the metrics service’s own Redis client topology.

### Proxy mode
| Env | Meaning |
|---|---|
| `METRICS_INGRESS_BASE_URL` | Base URL for ingress service (e.g. `http://ingress:8010`). |
| `METRICS_PROC_BASE_URL` | Base URL for a service exposing proc monitoring data via `/monitoring/system`. The current proc app does not expose that route directly. |
| `METRICS_AUTH_HEADER_NAME` | Header name for auth (e.g. `Authorization`). |
| `METRICS_AUTH_HEADER_VALUE` | Header value (e.g. `Bearer <token>`). |

### Scheduler
| Env | Default | Meaning |
|---|---|---|
| `METRICS_SCHEDULER_ENABLED` | `0` | Run periodic export loop. |
| `METRICS_EXPORT_INTERVAL_SEC` | `30` | Export interval. |
| `METRICS_EXPORT_ON_START` | `1` | Export once at boot. |
| `METRICS_RUN_ONCE` | `0` | Run once and exit (EventBridge task). |

### CloudWatch
| Env | Meaning |
|---|---|
| `METRICS_EXPORT_CLOUDWATCH` | Enable CloudWatch export. |
| `METRICS_CLOUDWATCH_NAMESPACE` | Namespace (default `KDCube/Metrics`). |
| `METRICS_CLOUDWATCH_REGION` | AWS region. |
| `METRICS_CLOUDWATCH_DIMENSIONS_JSON` | JSON dict of dimensions (tenant/project/env). For compatibility it also accepts ECS-style arrays such as `[{"Name":"Environment","Value":"staging"}]`. |

### Prometheus
| Env | Meaning |
|---|---|
| `METRICS_EXPORT_PROMETHEUS_PUSH` | Enable Pushgateway export. |
| `METRICS_PROM_PUSHGATEWAY_URL` | Pushgateway URL. |
| `METRICS_PROM_JOB_NAME` | Job label. |
| `METRICS_PROM_GROUPING_LABELS_JSON` | Grouping labels (tenant/project). |
| `METRICS_PROM_SCRAPE_TTL_SEC` | Cache TTL for `/metrics`. |

### Metric mapping
| Env | Meaning |
|---|---|
| `METRICS_MAPPING_JSON` | Optional mapping from internal metric keys to names/units. |

Example:
```json
{
  "ingress.sse.saturation_ratio": { "name": "chat/ingress/sse/saturation", "unit": "None" },
  "proc.queue.utilization_ratio": { "name": "chat/proc/queue/utilization", "unit": "None" },
  "proc.queue.wait.p95_ms": { "name": "chat/proc/queue/wait/p95", "unit": "Milliseconds" },
  "proc.exec.p95_ms": { "name": "chat/proc/exec/p95", "unit": "Milliseconds" }
}
```

CloudWatch naming note:
- the exporter replaces `.` with `/` when it sends metric names to CloudWatch
- if you want a stable CloudWatch name, provide it explicitly in `METRICS_MAPPING_JSON`
- in the current ECS deployment, the main proc metrics appear as:
  - `chat/proc/queue/utilization`
  - `chat/proc/queue/wait/p95`
  - `chat/proc/exec/p95`

---

## 5) What is `METRICS_INGRESS_BASE_URL`?

This is **only used in proxy mode**.  
It tells the Metrics server where the **ingress** API is so it can call:

`GET <METRICS_INGRESS_BASE_URL>/monitoring/system`

If you use Redis mode, you do **not** need it.

---

## 6) How to run (Redis mode)

```bash
METRICS_MODE=redis \
REDIS_URL=redis://... \
GATEWAY_CONFIG_JSON='{"tenant":"<TENANT_ID>","project":"<PROJECT_ID>"}' \
python -m kdcube_ai_app.apps.metrics.web_app
```

---

## 7) How to run (Proxy mode)

```bash
METRICS_MODE=proxy \
METRICS_INGRESS_BASE_URL=http://ingress:8010 \
# Optional only if another upstream exposes proc monitoring data:
# METRICS_PROC_BASE_URL=http://monitoring-upstream:8010 \
METRICS_AUTH_HEADER_NAME=Authorization \
METRICS_AUTH_HEADER_VALUE="Bearer <admin-token>" \
python -m kdcube_ai_app.apps.metrics.web_app
```

---

## 8) Security & Auth

### Redis mode (recommended)
- No user tokens.
- Metrics server is **internal only**.
- Secure via VPC / security groups.

### Proxy mode
- Requires an **admin token** to access `/monitoring/system`.
- Cognito tokens are **short‑lived**, so proxy mode is fragile unless you
  add service‑to‑service auth (mTLS or internal header allowlist).

---

## 9) ECS autoscaling integration

### Option A: Long‑running metrics service (recommended)
- Run `kdcube-metrics` as a service.
- Enable CloudWatch export.
- Configure ECS Auto Scaling to use the CloudWatch metrics.

### Option B: Scheduled exporter (EventBridge)
- Run `METRICS_RUN_ONCE=1` task on a schedule.
- Useful if you want no always‑on metrics service.

Sample ECS task definitions:
- `deployment/ecs/metrics/metrics-task-definition.json`
- `deployment/ecs/metrics/metrics-scheduled-task.json`

Template env file:
- `deployment/ecs/metrics/env.template`

---

## 10) Prometheus/HPA integration

Expose the Metrics server’s `/metrics` endpoint to Prometheus.
Then map metrics to HPA via Prometheus Adapter.

---

## 11) Recommended default (ECS)

For ECS, use Redis mode + CloudWatch export:

```env
METRICS_MODE=redis
METRICS_EXPORT_CLOUDWATCH=1
METRICS_SCHEDULER_ENABLED=1
METRICS_EXPORT_INTERVAL_SEC=30
```

## 11.1) CloudWatch export (quick start)

Minimum required:
```env
METRICS_EXPORT_CLOUDWATCH=1
METRICS_CLOUDWATCH_NAMESPACE=KDCube/Metrics
METRICS_CLOUDWATCH_REGION=eu-west-1
```

Optional dimensions (recommended for filtering):
```env
METRICS_CLOUDWATCH_DIMENSIONS_JSON='{"tenant":"tenant-id","project":"project-id","env":"prod"}'
```

On ECS/EC2 with IAM role:
```env
AWS_EC2_METADATA_DISABLED=false
NO_PROXY=169.254.169.254,localhost,127.0.0.1
```

Local dev (profile-based):
```env
AWS_SDK_LOAD_CONFIG=1
AWS_PROFILE=your-profile
```

---

## 12) Where metrics are computed

The same computation is reused from the chat monitoring endpoint:

- `src/kdcube-ai-app/kdcube_ai_app/infra/metrics/system_monitoring.py`
- Used by `/monitoring/system` in chat API.
- Used by Metrics server directly (redis mode). No proxy needed.

## 13) Endpoint behavior summary

### Redis mode
- `GET /metrics/ingress/system` → full aggregated system payload
- `GET /metrics/proc/system` → same full aggregated system payload
- `GET /metrics/combined` → `{ "timestamp", "mode": "redis", "system": ... }`
- `GET /metrics` → exported scalar metrics only

### Proxy mode
- `GET /metrics/ingress/system` → proxied ingress `/monitoring/system`
- `GET /metrics/proc/system` → proxied proc `/monitoring/system`
- `GET /metrics/combined` → separate `ingress` + `proc` payloads
- `GET /metrics` → exported scalar metrics derived from proxied responses

## 14) Feature requests

### 14.1) OTLP / OpenTelemetry metrics export

Requested feature:
- export the Metrics service output in **OpenTelemetry Metrics** format and push it
  to external systems through an OTLP exporter

Recommended scope for this service:
- keep the current `/metrics` Prometheus scrape endpoint unchanged
- add a third push exporter beside CloudWatch and Pushgateway:
  - OTLP gRPC
  - OTLP HTTP/protobuf
- support both:
  - scheduled periodic export
  - one-shot export

Recommended first-phase payload:
- export the **same scalar metric set** already produced by `extract_metrics(...)`
- treat them as point-in-time gauge observations
- do **not** invent histograms for current `p95` snapshot values
- do **not** treat current throttling-window snapshots as monotonic counters unless
  the upstream source is changed to cumulative counters

Recommended resource attributes:
- `service.name=metrics`
- `service.namespace=kdcube`
- `service.instance.id`
- `deployment.environment`
- `kdcube.tenant`
- `kdcube.project`
- `kdcube.metrics.mode`

Recommended config surface:
- `METRICS_EXPORT_OTLP`
- `METRICS_OTLP_ENDPOINT`
- `METRICS_OTLP_PROTOCOL=grpc|http/protobuf`
- `METRICS_OTLP_HEADERS_JSON`
- `METRICS_OTLP_TIMEOUT_SEC`
- `METRICS_OTLP_INSECURE`
- `METRICS_OTLP_COMPRESSION=gzip|none`
- `METRICS_OTLP_RESOURCE_ATTRIBUTES_JSON`

Phase-2 expansion candidates:
- ingress REST latency exports
- throttling windows by period
- DB connection pressure
- pool utilization aggregates
- circuit breaker state
- capacity-transparency metrics

### 14.2) Config alignment with descriptor-driven settings

Recommended follow-up:
- migrate Metrics service settings away from direct `os.getenv(...)` reads
- define a proper descriptor-backed metrics config block
- load that block through the shared settings layer, like ingress and proc

Until that is done, treat the Metrics service as a partially migrated component:
- shared platform/gateway config is descriptor-backed
- exporter/runtime knobs remain env-driven
