---
id: ks:docs/sdk/bundle/build/design/@longrun-README.md
title: "Bundle Longrun Design"
summary: "Design proposal for bundle-owned long-running runtime tasks, cooperative cancellation, future stream/channel consumption, concurrent listeners, and durable aggregation patterns."
status: proposal
updated_at: 2026-05-20
tags: ["sdk", "bundle", "design", "longrun", "streams", "telemetry", "lifecycle"]
keywords: ["bundle longrun", "bundle listener", "stream subscription", "telemetry consumer", "cooperative cancellation", "bundle lifecycle", "durable aggregation", "concurrent workers"]
see_also:
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-node-backend-bridge-README.md
  - ks:docs/sdk/node/node-backend-sidecar-README.md
  - ks:docs/service/streams/README.md
  - ks:docs/service/streams/telemetry-README.md
  - ks:docs/service/streams/background-jobs-README.md
---
# Bundle Longrun Design

This is a design proposal. It is not a current SDK contract.

The goal is to define the base lifecycle needed before KDCube adds bundle
stream listeners. The base primitive should be `longrun`, not `subscribe`.

A subscription is only one thing a longrun method may do. A longrun method is a
bundle-owned coroutine that the platform can start, supervise, reconfigure,
cancel, and restart as the environment changes.

## Why Longrun Comes First

The first missing primitive is:

```text
let this bundle run cooperative background logic while the bundle is enabled
```

Stream subscription can then be layered on top:

```text
inside that longrun method, open a telemetry channel and consume batches
```

That separation matters because a bundle may need long-running behavior that is
not a stream subscription:

- maintain a local cache
- keep a provider session warm
- run a compaction loop
- consume a Redis/Kafka stream
- run a periodic rollup scheduler
- watch a bundle-owned external resource

The common part is lifecycle and cancellation. The channel API is optional
runtime support inside that lifecycle.

## Proposed Bundle Shape

Multiple longrun methods may exist on one bundle.

Example shape:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, longrun


@bundle_entrypoint(...)
class TelemetryEntrypoint(BaseEntrypoint):
    @longrun(
        name="telemetry-consumer",
        scope="per_worker",
        enabled_prop="telemetry.consumer.enabled",
        restart_props=[
            "telemetry.stream",
            "telemetry.consumer_group",
        ],
        live_props=[
            "telemetry.batch_size",
            "telemetry.flush_interval_sec",
        ],
    )
    async def telemetry_consumer(self, ctx):
        async with ctx.open_channel("telemetry.events") as channel:
            async for batch in channel.batches(
                max_items=ctx.props["telemetry"].get("batch_size", 100),
                cancel_event=ctx.cancel_event,
            ):
                await self._store_raw_events(batch.events)
                await channel.ack(batch)

                if ctx.reconfigure_event.is_set():
                    if ctx.restart_requested:
                        return
                    ctx.reconfigure_event.clear()

    @longrun(
        name="rollup-sweeper",
        scope="leased_singleton",
        enabled_prop="telemetry.rollups.enabled",
    )
    async def rollup_sweeper(self, ctx):
        while not ctx.cancel_event.is_set():
            await self._recompute_dirty_rollups()
            await ctx.sleep(30)
```

Important points:

- `@longrun` methods must be async.
- One bundle may define several longruns.
- The platform owns start, cancellation, restart, and health.
- The bundle owns the actual logic.
- A longrun method should return cleanly when the cancellation event is set.
- A longrun method must not rely on request-local `self.comm` or current user
  state.

## Runtime Model

The future runtime should add a supervisor inside each proc worker.

```text
proc worker starts
  |
  | load bundle registry for tenant/project
  | discover @longrun descriptors
  | evaluate enabled props
  v
longrun supervisor
  |
  | start enabled methods for this worker/scope
  | pass LongrunContext
  | record heartbeat/health
  v
bundle @longrun method
  |
  | optional: open channel/subscription
  | optional: hold lease
  | optional: run timer loop
  v
durable effects
  |
  | raw events / rollups / cache / checkpoints
  v
cooperative stop on cancel_event
```

The longrun supervisor is similar in spirit to the Node sidecar helper:

- it is scoped to the loaded bundle runtime
- it starts work inside the current proc worker
- it has a lifecycle separate from individual API/MCP/chat requests
- bundle reload or relevant startup config drift stops the running work
- props changes may either live-reconfigure or request a restart

Unlike the Node sidecar, `@longrun` is not a child process by default. It is an
async task hosted by proc unless the bundle itself starts an internal sidecar.

## Longrun Context

The context should be explicit and serializable enough to reason about.

Proposed surface:

| Field or method | Purpose |
| --- | --- |
| `tenant`, `project`, `bundle_id` | Environment identity. |
| `longrun_name` | Decorated method identity. |
| `worker_id` | Current proc worker identity. |
| `instance_id` | Unique runtime generation for this longrun start. |
| `cancel_event` | Set when the method should stop soon. |
| `reconfigure_event` | Set when effective props changed and the method may adjust live behavior. |
| `restart_requested` | True when changed props require a clean method exit and restart. |
| `props` | Current effective bundle props snapshot. |
| `refresh_props()` | Refresh and return effective bundle props. |
| `control_events()` | Async iterator for cancellation/reconfigure/control events. |
| `sleep(seconds)` | Sleep that wakes early on cancellation. |
| `open_channel(name, **options)` | Open a stream/channel consumer helper. |
| `checkpoint.get/set(...)` | Bundle longrun checkpoint storage. |
| `heartbeat(status=...)` | Report liveness and optional lag/position. |
| `logger` | Longrun-scoped logger. |

The context must not expose request-bound communicator state. A longrun is a
system/bundle runtime path, not a user request path.

## Lifecycle

### Discovery

```text
bundle module import
  |
  v
loader discovers @longrun descriptors
  |
  | no longrun starts during import
  v
registry records descriptor metadata
```

Descriptor metadata should include:

- name
- method
- scope
- enabled prop path
- restart-scoped prop paths
- live-scoped prop paths
- optional channel declarations
- retry/backoff policy
- shutdown grace timeout

### Start

```text
worker runtime ready
  |
  | bundle enabled for tenant/project
  | enabled_prop resolves true
  | scope permits this worker to run it
  v
supervisor creates LongrunContext
  |
  v
asyncio task starts @longrun method
```

The first implementation can start longruns lazily after bundle load or eagerly
after proc applies the bundle registry. The important rule is that the runtime,
not request handlers, owns the supervisor.

### Props Change

Props changes should be split into live config and restart config.

```text
effective bundle props changed
  |
  v
longrun supervisor compares fingerprints
  |
  +--> live-only change
  |       set reconfigure_event
  |       ctx.props becomes current
  |
  +--> restart-scoped change
  |       set restart_requested
  |       set cancel_event
  |       restart after clean exit if still enabled
  |
  +--> enabled_prop false
          set cancel_event
          do not restart
```

This gives the bundle a way to reconsider long-lived work when configuration
changes. For example, a telemetry consumer may keep running when `batch_size`
changes, but it should exit and restart when its stream key, consumer group, or
privacy policy changes.

### Bundle Reload Or Platform Upgrade

```text
bundle code reload / registry update / proc shutdown
  |
  v
supervisor sets cancel_event
  |
  | wait grace timeout
  v
longrun exits cleanly
  |
  v
new code/config may start new generation
```

The runtime should log and mark unhealthy if a longrun ignores cancellation.
The design should prefer cooperative shutdown first. A hard task cancellation is
a last resort and must be treated like a crash: unacked stream messages may be
redelivered and durable state must remain consistent.

### Failure And Restart

```text
@longrun raises
  |
  v
supervisor records failure
  |
  | exponential backoff
  | bounded log noise
  v
restart if bundle still enabled
```

Longrun code must assume at-least-once effects. If it crashes after writing
durable state but before acking the channel, the same event or batch may return.

## Scope Options

The initial design should support these scopes conceptually, even if the first
runtime implementation starts with only one of them.

| Scope | Meaning | Use case |
| --- | --- | --- |
| `per_worker` | One longrun task in every proc worker that has the bundle loaded. | Stream consumer group members. |
| `leased_singleton` | At most one active task for tenant/project/bundle/name, guarded by a Redis lease. | Rollup sweeper, compaction loop. |
| `per_shard` | A logical set of shard owners distributed across workers. | High-volume telemetry partitions. |

For telemetry consumers, `per_worker` is acceptable when all workers join the
same stream consumer group. For rollup recomputation, `leased_singleton` is
usually safer unless the rollup code is explicitly sharded.

## Channel Subscription Is A Runtime Helper

The channel helper should hide Redis/Kafka mechanics while preserving the
semantics that matter to bundle code.

```text
@longrun method
  |
  | ctx.open_channel("telemetry.events")
  v
channel consumer
  |
  | read batch
  | expose events + positions
  v
bundle handler
  |
  | validate / normalize
  | durable write
  v
channel ack
```

The helper should make these choices explicit:

- channel name
- backend family: Redis Stream, Kafka, or in-process test channel
- consumer group
- consumer name
- shard/partition assignment
- batch size
- idle timeout
- pending retry/claim policy
- ack policy
- dead-letter policy

The bundle should not call `XACK` or Kafka offset commit directly unless it is
intentionally bypassing the SDK helper.

## Stream Consumption Contract

For Redis Streams:

```text
telemetry stream shard
  |
  | XREADGROUP group=<tenant/project/bundle/longrun>
  v
worker A longrun              worker B longrun
  |                           |
  | process different entries | process different entries
  v                           v
durable raw insert            durable raw insert
  |                           |
  | XACK after commit         | XACK after commit
  v                           v
pending entry cleared         pending entry cleared
```

Recovery path:

```text
worker crashes after durable insert before XACK
  |
  v
message remains pending
  |
  | XAUTOCLAIM by another worker after idle timeout
  v
duplicate delivery
  |
  | insert event_id primary key / idempotency ledger
  v
no duplicate durable effect
  |
  v
XACK
```

For Kafka, the same application rule applies:

- process in a consumer group
- write durable state first
- commit offset after durable state is safe
- dedupe by event id or processing ledger

The platform should document the delivery contract as at-least-once, not
exactly-once.

## Concurrent Listener Pattern

The expected stream listener shape is one longrun per worker in the same
consumer group.

```text
proc worker 1       proc worker 2       proc worker 3
  |                   |                   |
  v                   v                   v
@longrun A          @longrun A          @longrun A
  |                   |                   |
  +-------- same telemetry consumer group-+
                      |
                      v
             stream shards / partitions
                      |
                      v
              durable raw event store
                      |
                      v
              rollup tables / cache
```

Rules:

- every listener instance may see retries
- no listener owns authoritative in-memory aggregate state
- event order is only guaranteed inside the chosen stream partition/shard
- cross-shard aggregation must be done in durable storage
- ack only after durable effects are complete
- cancellation must stop polling and finish or abandon the current batch
  according to the channel ack policy

## Aggregation Patterns

Aggregation must not rely on the stream consumer's process memory. Use one of
these durable patterns.

### Pattern 1: Raw First, Recompute Rollups

```text
longrun consumer
  |
  | idempotent insert by event_id
  v
raw events table
  |
  | scheduled rollup job / leased_singleton longrun
  v
rollup table
  |
  v
Redis widget cache
```

Use this first. It is the easiest durable model to reason about.

Properties:

- raw events are the source of truth
- rollups are disposable and recomputable
- retry cannot inflate aggregates if raw insert is idempotent
- rollup repair is possible after code changes

### Pattern 2: Raw Insert Plus Transactional Increment

```text
begin transaction
  |
  | insert event_id into processing ledger
  | if inserted:
  |   insert raw event
  |   upsert rollup counters
  v
commit
  |
  v
ack channel message
```

Use this when rollups must be visible immediately. The event ledger or raw
event primary key must be inside the same transaction as the rollup increment.

### Pattern 3: Shard-Local Partial Rollups

```text
stream shard N
  |
  v
worker longrun
  |
  | write partial aggregate for shard N + bucket
  v
partial_rollups
  |
  | merge job
  v
final_rollups
```

Use this only when volume requires it. It adds operational complexity but keeps
write contention lower.

### Pattern 4: Redis Cache As Acceleration Only

```text
raw event / SQL rollup
  |
  v
Redis cache for dashboard widgets
  |
  v
UI reads fast summary
```

Redis counters can make read surfaces fast, but they should not be the only
source of truth for data that must survive replay, repair, or privacy deletion.

## Telemetry Consumer Data Flow

Same-environment flow:

```text
chat proc / MCP / API / frontend / bundle SDK
  |
  | build telemetry envelope
  v
telemetry emit(...)
  |
  +--> operations REST ingest
  |       |
  |       | auth from current KDCube user/session
  |       v
  |   validate + privacy gate
  |
  +--> platform hook XADD telemetry stream
          |
          v
      telemetry stream shards
          |
          v
      bundle @longrun telemetry-consumer
          |
          | idempotent raw insert
          | ack after durable insert
          v
      raw event store
          |
          v
      rollup recompute / transactional increment
          |
          v
      Redis widget cache
          |
          v
      read API / UI / MCP surface
```

Cross-environment flow:

```text
source KDCube environment
  |
  | telemetry SDK emit over HTTPS
  v
collector public ingest
  |
  | explicit auth / signature / tenant mapping
  | validate envelope
  v
raw event store or local telemetry stream
  |
  v
bundle @longrun consumer
  |
  v
rollups + cache + read APIs
```

Cancellation and upgrade path:

```text
operator updates bundle props or platform reloads bundle
  |
  v
longrun supervisor
  |
  | set reconfigure_event for live props
  | or set cancel_event for restart props/code reload
  v
bundle @longrun method
  |
  | finish current durable write
  | ack only completed batch
  | return cleanly
  v
supervisor starts next generation if still enabled
```

## Backpressure

Longrun consumers must have explicit lag behavior.

Recommended controls:

- batch size
- max in-flight batches per longrun
- max processing time before heartbeat warning
- pending idle timeout before claim
- dead-letter threshold by delivery count or age
- consumer health with current lag and last acked position

If a collector falls behind, producers should not block request paths for long.
The ingestion layer should accept quickly, write to a local stream or raw table,
and let longrun workers catch up.

## Privacy And Deletes

Longrun stream consumers must preserve telemetry privacy policy:

- do not store raw prompt/answer text by default
- normalize or reject unexpected content fields before raw insert
- use low-cardinality dimensions for rollups
- keep user deletion repairable by raw event delete plus rollup recompute
- keep cache invalidation separate from source-of-truth deletion

Privacy deletion flow:

```text
usage_forget(user_id)
  |
  v
delete raw events for user scope
  |
  v
mark affected rollup buckets dirty
  |
  v
leased rollup longrun recomputes
  |
  v
cache refresh
```

## Relationship To Existing Surfaces

| Surface | Difference from `@longrun` |
| --- | --- |
| `@api(...)` | Request/response operation. It should not hold a long polling loop. |
| `@mcp(...)` | Tool/server surface. It is invoked by a client, not supervised as background runtime. |
| `@cron(...)` | Due scan. It should decide what is due and enqueue work, not run forever. |
| `@on_job` | Ready background job execution. It handles one claimed job envelope and returns. |
| `on_bundle_load(...)` | One-time preparation. It should not become a never-ending loop. |
| `on_props_changed(...)` | Reconcile active instance state after props change. It can signal helpers, but it is not the main loop. |
| Node sidecar | Process-local child process. `@longrun` is an in-proc supervised coroutine unless it starts its own sidecar. |

## Implementation Dependency Boundary

Application bundles must not depend on this lifecycle until the runtime
supervisor and channel helper exist.

Recommended platform sequence:

1. Keep current bundle surfaces request/job oriented.
2. Define the longrun descriptor and lifecycle metadata.
3. Implement `@longrun` supervisor with cooperative cancellation and
   `per_worker` scope.
4. Add channel helper for Redis Streams.
5. Add `leased_singleton` scope for sweepers and repair workers.
6. Add Kafka channel backend only when Redis stream semantics are insufficient.

Until then, stream-backed applications should use explicit ingestion endpoints,
durable storage, and scheduled or job-based repair paths.
