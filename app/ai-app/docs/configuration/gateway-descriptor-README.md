---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
title: "Gateway Policy Descriptor"
summary: "Gateway runtime policy configuration in gateway.yaml: guarded and bypassed routes, rate limits, Data Bus publish limits, backpressure, capacity, pools, and circuit-breaker behavior for ingress and proc."
tags: ["service", "configuration", "gateway", "policy", "deployment", "descriptor"]
keywords: ["gateway admission control", "guarded routes", "bypass routes", "rate limiting policy", "backpressure thresholds", "service capacity", "connection pool sizing", "circuit breaker settings", "monitoring flags", "ingress and processor policy"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/gateway-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
---
# Gateway Policy Descriptor

`gateway.yaml` is the gateway runtime policy descriptor.

It controls:

- which ingress REST endpoints are treated as work-creating gateway traffic
- which endpoints bypass throttling
- per-role rate limits
- per-role Socket.IO Data Bus publish package limits
- capacity and backpressure thresholds
- gateway-owned Redis and Postgres pool sizing
- SSE and integrations concurrency caps
- tenant/project namespacing for gateway cache and coordination keys

It does not provide application settings through `get_settings()`,
`read_plain(...)`, or `get_secret(...)`.

The gateway subsystem loads this file into `GatewayConfiguration` in
[config.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/gateway/config.py).

## Direct runtime contract

### Supported access path

| Need | Supported mechanism | Notes |
|---|---|---|
| full effective gateway config override | `GATEWAY_CONFIG_JSON` | highest precedence |
| explicit YAML file path | `GATEWAY_YAML_PATH` | runtime reads this file directly |
| descriptor directory fallback | `PLATFORM_DESCRIPTORS_DIR/gateway.yaml` | used when `GATEWAY_YAML_PATH` is unset |
| component selection for component-scoped sections | `GATEWAY_COMPONENT` | selects `ingress` or `proc` payload |
| force env/descriptor config on startup over cached Redis config | `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP`; assembly path `platform.services.<component>.service.gateway_config_force_env_on_startup` | gateway bootstrap/admin flow |

There is no supported `get_plain(...)`, `get_settings()`, or `get_secret(...)`
surface for individual `gateway.yaml` fields.

### Loader precedence

Gateway config is loaded in this order:

1. `GATEWAY_CONFIG_JSON`
2. `GATEWAY_YAML_PATH`
3. `PLATFORM_DESCRIPTORS_DIR/gateway.yaml`
4. code/env defaults from `GatewayConfigFactory.create_from_env()`

The YAML file may be either:

- a bare mapping
- or a file with a top-level `gateway:` key

Both are accepted by `_load_gateway_yaml()`.

Operational note: a running environment may already have an effective gateway
config cached in Redis. Editing `gateway.yaml` alone does not replace that
cached value. For local/debug descriptor-driven runs, set
`platform.services.<component>.service.gateway_config_force_env_on_startup:
true` in `assembly.yaml` and restart the component so startup reloads
`gateway.yaml` instead of the Redis cache. This is the expected path for local
gateway policy changes such as raising the proc anonymous burst limit for public
MCP documentation clients.

## YAML shape

The supported descriptor shape is:

```yaml
gateway:
  tenant: "demo-tenant"
  project: "demo-project"
  profile: "development"

  guarded_rest_patterns:
    ingress: [...]
    proc: [...]

  bypass_throttling_patterns:
    ingress: [...]
    proc: [...]

  service_capacity:
    ingress: {...}
    proc: {...}

  backpressure:
    capacity_source_component: "proc"
    ingress: {...}
    proc: {...}

  rate_limits:
    ingress: {...}
    proc: {...}

  data_bus:
    ingress:
      publish_limits: {...}

  pools:
    ingress: {...}
    proc: {...}
    pg_max_connections: 100

  limits:
    ingress: {...}
    proc: {...}

  redis:
    sse_stats_ttl_seconds: 60
    sse_stats_max_age_seconds: 120
```

Supported component aliases:

- `ingress`, `rest`, `chat-rest`, `chat_rest` -> ingress
- `proc`, `processor`, `worker`, `chat-proc`, `chat_proc` -> proc

If a section is already flat and has no component keys, the same payload is used
as-is.

## Required top-level fields

| Path | Required | Effect |
|---|---|---|
| `gateway.tenant` or `gateway.tenant_id` | yes | gateway cache/coordination namespace |
| `gateway.project` or `gateway.project_id` | yes | gateway cache/coordination namespace |
| `gateway.profile` | no | tuning profile label; unknown values fall back to `development` |

If `tenant` or `project` is missing, gateway config loading fails.

## What each section does

### `guarded_rest_patterns`

`guarded_rest_patterns` is a list of regexes that classify ingress REST
endpoints as `CHAT_INGRESS`.

That matters because `CHAT_INGRESS` requests go through the full gateway
admission path:

- throttling
- gate checks
- backpressure

Unmatched REST endpoints are treated as `READ`.

Runtime consumer:
- [gateway_policy.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/middleware/gateway_policy.py)

Important current behavior:

- for `ingress`, this list actively controls REST endpoint classification
- for `proc`, the current resolver already treats `/api/integrations/...` as
  `CHAT_INGRESS` regardless of the configured list
- if the section is missing or empty, the loader falls back to built-in default
  guarded patterns from [config.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/gateway/config.py)

Public bundle operations can be served with a trailing sub-path —
`…/integrations/bundles/{tenant}/{project}/{bundle}/public/{alias}/{rest}` — for
example an app that returns a shareable standalone page. For those, the public
pattern must allow the tail: use `…/public/[^/]+(?:/.*)?$` (not
`…/public/[^/]+$`) so the sub-path request is still classified `CHAT_INGRESS`
and reaches the operation (which receives `{rest}` as its `path_tail`). The
built-in defaults in `gateway_policy.py` already use the tail-allowing form, so
update this descriptor list only if you override it.

### `bypass_throttling_patterns`

`bypass_throttling_patterns` is a list of regexes for endpoints that should
skip throttling counters.

What it does:

- bypasses throttling only
- does not bypass session resolution
- does not bypass gate checks
- does not bypass backpressure for guarded ingress traffic

Current resolver behavior:

- the bypass is applied only when the endpoint is already classified as
  `READ` or `CONNECT`
- it does not turn a guarded `CHAT_INGRESS` endpoint into bypassed traffic

Runtime consumer:
- [gateway_policy.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/middleware/gateway_policy.py)

If the section is missing or invalid, the effective list is empty.

### `rate_limits`

`rate_limits` defines per-role throttling policy.

Supported per-role fields:

| Path | Type | Meaning |
|---|---|---|
| `hourly` | integer | hourly quota; `-1` means unlimited |
| `burst` | integer | short-window burst allowance |
| `burst_window` | integer | burst window length in seconds |

Supported roles:

- `anonymous`
- `registered`
- `paid`
- `privileged`

Default values when the section is omitted:

| Role | `hourly` | `burst` | `burst_window` |
|---|---:|---:|---:|
| `anonymous` | 120 | 10 | 60 |
| `registered` | 600 | 30 | 60 |
| `paid` | 2000 | 60 | 60 |
| `privileged` | -1 | 200 | 60 |

Validation rules:

- `hourly` must be positive or `-1`
- `burst` must be positive
- the four standard roles should exist in the final effective config

### `data_bus.publish_limits`

`data_bus.ingress.publish_limits` defines the Socket.IO `data_bus.publish`
package admission policy.

This is a streaming policy. One connected Socket.IO client can send many
`data_bus.publish` packages and each package can carry many durable
`messages[]`. The Data Bus policy therefore counts packages, messages, and
bytes before writing to the bundle-scoped Redis Stream.

It is separate from:

- generic `rate_limits`, which count gateway requests
- OpenResty request-size and connection controls, which still apply at the
  proxy layer
- `chat_message`, which remains conversation ingress and uses
  `external_events[]`

Supported per-role fields:

| Path | Type | Meaning |
|---|---|---|
| `enabled` | boolean | enables this role's app-layer Data Bus publish package policy; default `true`; set `false` for prototyping to bypass package/message/byte counters for that role |
| `packages_per_minute` | integer | maximum `data_bus.publish` packages per session window; `-1` means unlimited |
| `messages_per_minute` | integer | maximum total messages accepted from packages in the window; `-1` means unlimited |
| `bytes_per_minute` | integer | maximum total JSON package bytes in the window; `-1` means unlimited |
| `max_messages_per_package` | integer | maximum `messages[]` length in one package; `-1` means unlimited |
| `max_package_bytes` | integer | maximum JSON package size before durable write; `-1` means unlimited |
| `window_seconds` | integer | counter window length; default `60` |

Default values:

| Role | packages/min | messages/min | bytes/min | max messages/package | max package bytes | window |
|---|---:|---:|---:|---:|---:|---:|
| `anonymous` | 60 | 600 | 2097152 | 50 | 262144 | 60 |
| `registered` | 600 | 6000 | 16777216 | 200 | 1048576 | 60 |
| `paid` | 1200 | 12000 | 33554432 | 500 | 2097152 | 60 |
| `privileged` | -1 | -1 | -1 | 1000 | 4194304 | 60 |

Example:

```yaml
gateway:
  data_bus:
    ingress:
      publish_limits:
        registered:
          packages_per_minute: 600
          messages_per_minute: 6000
          bytes_per_minute: 16777216
          max_messages_per_package: 200
          max_package_bytes: 1048576
          window_seconds: 60
```

Runtime consumers:

- policy shape:
  [policy.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/data_bus/policy.py)
- limiter:
  [data_bus_limiter.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/gateway/data_bus_limiter.py)
- Socket.IO publish handler:
  [publish.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/socketio/data_bus/publish.py)

Validation rules:

- per-role `enabled` defaults to `true`
- numeric limits must be `>= 0` or `-1`
- `window_seconds` must be positive

Prototype/off switch:

```yaml
gateway:
  data_bus:
    ingress:
      publish_limits:
        registered:
          enabled: false
```

This disables only the Data Bus app-layer package/message/byte policy for
registered sessions. The Socket.IO protocol shape checks, auth/session checks,
bundle visibility checks, and OpenResty/proxy limits still apply.

### `service_capacity`

`service_capacity` defines the gateway capacity model for each component.

Supported fields:

| Path | Type | Meaning |
|---|---|---|
| `concurrent_requests_per_process` | integer | how many work requests one worker process can handle concurrently |
| `avg_processing_time_seconds` | number | average work duration used for throughput estimates |
| `processes_per_instance` | integer | number of worker processes per service instance |
| `concurrent_requests_per_instance` | integer | accepted by loader, but normally derived |
| `requests_per_hour` | integer | accepted by loader, used only as an optional override |

Core formulas:

- processing capacity per instance =
  `concurrent_requests_per_process * processes_per_instance`
- effective concurrent capacity =
  `processing_capacity * (1 - capacity_buffer)`
- queue capacity per instance =
  `processing_capacity * queue_depth_multiplier`

This section drives:

- gateway capacity analysis and validation
- backpressure thresholds
- throughput estimates shown by monitoring/admin tools
- pool defaults when explicit pool caps are omitted

Validation rules:

- `concurrent_requests_per_process > 0`
- `processes_per_instance > 0`
- `avg_processing_time_seconds > 0`
- `concurrent_requests_per_instance` must match
  `concurrent_requests_per_process * processes_per_instance`

### `backpressure`

`backpressure` defines how queue pressure is converted into admission
rejections.

Supported fields:

| Path | Type | Meaning |
|---|---|---|
| `capacity_buffer` | number | reserved capacity fraction removed from effective concurrent capacity |
| `queue_depth_multiplier` | number | queue size multiplier relative to processing capacity |
| `anonymous_pressure_threshold` | number | queue pressure ratio at which anonymous traffic is blocked |
| `registered_pressure_threshold` | number | queue pressure ratio at which registered traffic is blocked |
| `paid_pressure_threshold` | number | queue pressure ratio at which paid traffic is blocked |
| `hard_limit_threshold` | number | queue pressure ratio at which everyone is blocked |
| `capacity_source_component` | string | which component heartbeat supplies the live capacity model |

`capacity_source_component` accepted values:

- `proc`, `processor`, `chat-proc`, `chat_proc` -> `chat/proc`
- `ingress`, `rest`, `chat-rest`, `chat_rest` -> `chat/rest`
- `service_type:service_name` -> explicit selector

Runtime effect:

- the backpressure layer counts healthy heartbeat publishers for the selected
  source component
- it derives actual system capacity from their reported `max_capacity`
- it compares current queue depth with thresholds computed from that capacity

Validation rules:

- every threshold must be in `(0, 1]`
- `capacity_buffer` must be in `(0, 1)`
- `queue_depth_multiplier > 0`
- ordering must be:
  - `anonymous < registered <= paid < hard_limit`

### `pools`

`pools` controls gateway-owned Postgres and Redis client caps.

Supported fields:

| Path | Type | Meaning |
|---|---|---|
| `pg_pool_min_size` | integer | per-process Postgres pool minimum |
| `pg_pool_max_size` | integer | per-process Postgres pool maximum |
| `redis_max_connections` | integer | per-process Redis client pool maximum |
| `pg_max_connections` | integer | database capacity reference used for monitoring/warnings |

Runtime consumers:

- ingress Postgres pools in
  [resolvers.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/resolvers.py)
- retrieval/KB Postgres pools in
  [kb_client.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/retrieval/kb_client.py)
- shared Redis clients in
  [client.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/redis/client.py)

Effective defaults:

- `pg_pool_min_size` defaults to `0`
- `pg_pool_max_size` defaults to `service_capacity.concurrent_requests_per_process`
- `redis_max_connections` has no cap when unset

Operational note:

- one process typically uses three Redis pools
  (`async`, `async_decode`, `sync`)
- approximate Redis connections per process are therefore
  `3 * redis_max_connections`

Validation rules:

- `pg_pool_min_size >= 0`
- `pg_pool_max_size >= 0`
- `pg_pool_max_size >= pg_pool_min_size`
- `redis_max_connections > 0` when set
- `pg_max_connections > 0` when set

### `limits`

`limits` holds component-specific hard or soft service caps.

Supported fields:

| Path | Type | Meaning | Runtime consumer |
|---|---|---|---|
| `max_sse_connections_per_instance` | integer | ingress SSE connection cap per instance | [chat.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py) |
| `max_integrations_ops_concurrency` | integer | proc integrations concurrency cap | [integrations.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py) |
| `max_queue_size` | integer | absolute queue-size ceiling checked by backpressure Lua admission | [backpressure.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/gateway/backpressure.py) |

Backward compatibility:

- the loader still accepts `max_integrations_concurrency`
- it is normalized into `max_integrations_ops_concurrency`

Validation rules:

- these values must be `>= 0` when set
- `max_queue_size = 0` or unset means no explicit hard queue cap

### `redis`

`redis` controls gateway Redis TTLs and SSE stats retention.

Supported fields:

| Path | Type | Meaning |
|---|---|---|
| `rate_limit_key_ttl` | integer | rate-limit counter TTL |
| `session_ttl` | integer | session cache TTL |
| `analytics_ttl` | integer | analytics TTL |
| `circuit_breaker_stats_ttl` | integer | circuit-breaker stats TTL |
| `heartbeat_ttl` | integer | process heartbeat TTL |
| `sse_stats_ttl_seconds` | integer | TTL for ingress SSE stats entries in Redis |
| `sse_stats_max_age_seconds` | integer | freshness window for SSE stats consumers |

Current concrete consumers:

- ingress SSE stats publisher uses `sse_stats_ttl_seconds`
- system monitoring uses `sse_stats_max_age_seconds`

### `monitoring`

`monitoring` is supported by the loader even though it is usually not present in
the default deployment template.

Supported fields:

- `throttling_events_retention_hours`
- `session_analytics_enabled`
- `circuit_breaker_stats_retention_hours`
- `queue_analytics_enabled`
- `heartbeat_timeout_seconds`
- `instance_cache_ttl_seconds`

### `circuit_breakers`

`circuit_breakers` is also supported directly by the loader.

Authentication keys:

- `auth_failure_threshold`
- `auth_recovery_timeout`
- `auth_success_threshold`
- `auth_window_size`
- `auth_half_open_max_calls`

Rate-limiter keys:

- `rate_limit_failure_threshold`
- `rate_limit_recovery_timeout`
- `rate_limit_success_threshold`
- `rate_limit_window_size`
- `rate_limit_half_open_max_calls`

Backpressure keys:

- `backpressure_failure_threshold`
- `backpressure_recovery_timeout`
- `backpressure_success_threshold`
- `backpressure_window_size`
- `backpressure_half_open_max_calls`

### `redis_url`

`redis_url` is an optional gateway-specific Redis override.

If it is absent, runtime falls back to `get_settings().REDIS_URL`.

### `instance_id`

The loader accepts `instance_id`, but it should not be treated as the
descriptor authority for replicas.

Runtime behavior:

- `GatewayConfigFactory.create_from_env()` replaces it with `INSTANCE_ID` when
  building the live config from YAML or JSON
- per-replica identity should therefore come from `INSTANCE_ID`, not from the
  shared descriptor file

## CLI compose descriptor mode

In descriptor-seeded CLI compose installs, the current supported path is:

1. the installer stages `gateway.yaml` into the runtime workspace
2. main compose `.env` points `HOST_GATEWAY_YAML_DESCRIPTOR_PATH` to that staged
   file
3. service env files point runtime to `/config` via `PLATFORM_DESCRIPTORS_DIR`

So the descriptor-source-of-truth path is the staged workspace file
`/config/gateway.yaml`, not an expanded field-by-field env surface.

## Direct local service run

For direct local `ingress` or `proc` runs:

- set `GATEWAY_YAML_PATH=/abs/path/to/gateway.yaml`
- or set `PLATFORM_DESCRIPTORS_DIR=/abs/path/to/descriptors`

The loader will then read `gateway.yaml` directly.

## Inspecting the effective component config

Use the built-in dump tool to verify what a process actually sees:

```bash
python -m kdcube_ai_app.infra.tools.gateway_config_dump --json
```

That output already applies:

- loader precedence
- component selection from `GATEWAY_COMPONENT`
- effective pool defaults
- effective limits used by the current process
