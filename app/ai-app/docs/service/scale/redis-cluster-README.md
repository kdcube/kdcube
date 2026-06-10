---
id: ks:docs/service/scale/redis-cluster-README.md
title: "Redis Cluster Readiness"
summary: "Scale notes for Redis Cluster compatibility, including multi-key Lua boundaries and the gateway capacity index."
tags: ["service", "scale", "redis", "redis-cluster", "lua", "gateway", "backpressure"]
keywords: ["redis cluster", "hash tags", "multi-key lua", "gateway capacity index", "queue admission", "redis streams"]
see_also:
  - ks:docs/service/maintenance/gateway-control-README.md
  - ks:docs/service/maintenance/requests-monitoring-README.md
  - ks:docs/service/comm/conversation-event-bus-orchestrator-README.md
---
# Redis Cluster Readiness

The current runtime is designed around Redis key prefixes scoped by tenant and
project. That works for a single Redis keyspace. Redis Cluster adds one extra
rule: every key touched by one Lua script must live in the same hash slot.

## Current Status

Gateway admission no longer discovers heartbeat keys with `KEYS` or `SCAN`.
Heartbeat writers maintain a bounded service-scoped capacity index:

```text
<tenant>:<project>:kdcube:system:capacity:process-index:<service_type>:<service_name>
```

Admission reads that index outside Lua, computes `healthy_processes` and
`actual_capacity`, and passes those numbers into the Lua admission script. The
Lua script therefore handles only the atomic mutation boundary:

```text
check queue pressure
write prepared conversation-lane events
enqueue one processor wake
increment capacity counter
```

## Cluster Boundary

The remaining Redis Cluster concerns are routing and key-slot boundaries, not
gateway key discovery.

## Client Layer

The runtime currently builds normal Redis clients from a single URL. The
cluster migration point is the Redis client factory:

```text
kdcube_ai_app/infra/redis/factory.py
```

The factory is additive first and currently creates standalone Redis clients.
Cluster topology is represented explicitly and rejected with a clear runtime
error until the cross-slot key design is wired through the call sites. The
existing helpers in `infra.redis.client` delegate to that factory, so current
call sites keep their imports while construction is centralized.

Call sites should not decide which Redis client type to construct. The factory
should expose the same app surface while choosing standalone Redis or Redis
Cluster from configuration.

Redis Cluster also supports only database 0. Cluster descriptors must not rely
on Redis logical database selection.

## Multi-Key Atomic Operations

The chat admission script touches several keys in one atomic operation:

```text
prompt queues by user type
in-flight queue lists
conversation mailbox counters
capacity counter
conversation event-lane stream
conversation event records
```

In Redis Cluster, those keys must share a hash tag. Without that, Redis Cluster
will reject the script with a cross-slot error.

The same rule applies to other atomic Redis operations:

| Area | Current pattern | Cluster concern |
| --- | --- | --- |
| Gateway admission / chat enqueue | Multi-key Lua in `infra/gateway/backpressure.py` | All queue, inflight, continuation-count, capacity-counter, lane-stream, and event-record keys in the script must share one slot. |
| Processor queue claim | `BRPOPLPUSH ready_queue inflight_queue` in `apps/chat/processor.py` | Source queue and destination inflight queue must share one slot. |
| Conversation event owner lease | Multi-key Lua in `apps/chat/external_events.py` for owner epoch, owner token, and owner record | Lease keys must share one conversation-lane slot. |
| Auth session merge/index repair | Lua in `auth/sessions.py` updates session plus index keys | All script-touched keys must be declared as keys and share one slot; constructing key names inside Lua from ARGV is not cluster-ready. |
| Economics limiter | Multi-key Lua in `apps/chat/sdk/infra/economics/limiter.py` | Subject policy counters, locks, reservation index/map, hourly buckets, and bundle indexes must share a deliberate economics slot or be split into separate atomic domains. |
| Project budget analytics | Multi-key Lua in `apps/chat/sdk/infra/economics/project_budget.py` | Spend counters and last-spend keys must share one budget slot for the script. |
| Lock release helpers | Single-key compare-and-delete Lua | Usually cluster-compatible if the cluster client routes by the one key. |

## Key Discovery

Gateway admission no longer walks Redis keys. Other operational and analytics
paths still use `KEYS` or `SCAN`-style discovery and need cluster-aware
replacement before they can report cluster-wide truth:

| Area | Current pattern | Cluster concern |
| --- | --- | --- |
| Runtime capacity/monitoring helpers | `keys(pattern)` in gateway definitions / system monitoring | A single-node key scan is incomplete in Redis Cluster. Use maintained indexes or a cluster-admin scan across all masters. |
| Control-plane Redis Browser | `scan(match=...)` | Must be explicitly cluster-aware if it is meant to inspect the whole cluster. |
| Legacy orchestration inspection | `keys(queue_pattern)` | Must use indexes or scan all masters. |
| Economics reporting fallbacks | `scan(match=...)` for bundle/provider discovery | Prefer maintained bundle/provider indexes; cluster-wide scan is only an admin fallback. |

## Localized Migration Point

This is localized enough to change deliberately:

1. Redis client construction flows through `infra.redis.factory`, with the
   existing shared helpers in `infra.redis.client` as the compatibility layer.
2. Key construction flows through runtime constants and namespace helpers.
3. Queue admission keys are assembled in the gateway/backpressure module.
4. Conversation lane keys are assembled by the external-event source.
5. Economics and budget keys are assembled in their limiter/store modules.
6. The atomic admission script receives all affected keys at one call site.

A Redis Cluster migration should add a stable hash tag to all keys that belong
to one atomic conversation admission domain. For example:

```text
{kdc:<tenant>:<project>:conv:<conversation_id>}:...
```

For operations that are not conversation-specific, use a different deliberate
domain, for example:

```text
{kdc:<tenant>:<project>:gateway}:...
```

Do not rely on tenant/project prefixes alone as a cluster slot strategy. Redis
Cluster hashes the whole key unless a `{...}` hash tag is present.

## Pub/Sub And Streams

Redis Streams operations are generally cluster-compatible when each operation
targets one stream key. The data-bus and job-stream runtimes should therefore
keep stream operations single-key and place any companion locks/cursors into a
documented hash-tag domain when they must be atomic with that stream.

Redis Pub/Sub must be validated with the chosen cluster client. The current
communicator/config-listener code assumes a simple Redis connection and normal
subscribe/publish semantics. A cluster deployment should explicitly decide
whether to use regular cluster Pub/Sub, sharded Pub/Sub, or replace critical
fanout with Streams when durability or slot locality matters.

## Design Rule

When adding a Redis Lua operation, document its atomic domain and keep every
key in that script under one hash tag. If a flow needs keys from multiple
domains, split the operation and make the cross-domain handoff explicit.

When adding discovery or monitoring flows, avoid keyspace scans in runtime
paths. Maintain explicit indexes as part of the write path, or mark the flow as
cluster-admin-only and scan all masters intentionally.
