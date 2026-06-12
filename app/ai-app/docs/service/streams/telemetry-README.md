---
id: repo:kdcube-ai-app/app/ai-app/docs/service/streams/telemetry-README.md
title: "Telemetry Streams"
summary: "Design boundary for usage and observability telemetry: event schema, ingestion modes, reliability, privacy, storage, and how collector bundles should consume events."
status: proposal
tags: ["service", "streams", "telemetry", "usage", "events", "redis", "kafka", "bundles"]
keywords: ["telemetry stream", "usage events", "collector bundle", "event ingestion", "mcp analytics", "tool usage", "comm recording", "comm event sink", "bundle listener"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/external-log-collector/frontend-events-design.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/external-log-collector/Architecture.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-system.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/README-comm.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-recording-event-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/design/@longrun-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
---
# Telemetry Streams

Telemetry records facts that already happened.

Examples:

- `chat.session.start`
- `chat.message`
- `mcp.call`
- `tool.invoke`
- `accounting.usage`
- `workflow.step`
- `comm.event`
- `client.log.warning`
- `error`

Telemetry is the stream family needed by usage analytics and observability.
It should be kept separate from background jobs and conversation scheduling.

## Current Status

There is no platform-level `@on_event` bundle listener today.

That means the first telemetry collector implementation should not depend on a bundle
subscribing directly to an internal bus. Use explicit ingestion APIs and durable
storage first. A future event-listener surface can consume the same envelope.

## Goals

- give telemetry and reporting bundles a stable event contract
- reuse existing producer paths such as `ChatCommunicator` where possible
- keep event emission cheap for producers
- dedupe events with `event_id`
- preserve privacy by default
- support same-KDCube and cross-KDCube collection
- support Redis Streams locally and Kafka later without changing the event
  envelope

## Non-goals

- telemetry does not execute bundle work per event
- telemetry does not own chat turn scheduling
- telemetry does not replace accounting storage
- telemetry does not require raw prompt or answer text

## Unified Event Shape

Telemetry should not introduce a separate event shape from the existing external
log collector. The current client collector already uses a base event context:

- `event_type`
- `origin`
- `tenant`
- `project`
- `user_id`
- `session_id`
- `conversation_id`
- `timestamp`
- `timezone`

The normalized telemetry envelope keeps that base and extends it with stable
identity, metric, privacy, and source fields needed by collectors.

Recommended normalized shape:

```json
{
  "schema": "kdcube.telemetry.v1",
  "event_id": "evt_...",
  "event_type": "metric",
  "origin": "chat-proc",
  "tenant": "default",
  "project": "main",
  "user_id": "user_123",
  "session_id": "session_123",
  "conversation_id": "conv_123",
  "timestamp": "2026-05-20T12:34:56.789Z",
  "timezone": "UTC",
  "source_kube": "kdcube-1",
  "source_component": "chat-proc",
  "source_bundle": "kdcube.copilot@2026-04-03-19-05",
  "user_type": "registered",
  "turn_id": "turn_123",
  "name": "tool.invoke",
  "value": 1.0,
  "tags": {
    "tool": "web_search",
    "model": "claude-sonnet-4-5"
  },
  "dimensions": {
    "tool": "web_search",
    "model": "claude-sonnet-4-5"
  },
  "metrics": {
    "latency_ms": 842,
    "tokens_in": 1200,
    "tokens_out": 220
  },
  "status": "success",
  "error_kind": null,
  "privacy": {
    "contains_content": false,
    "content_retention": "none"
  },
  "meta": {}
}
```

Field rules:

- `event_id` must be stable enough for dedupe on retry
- `timestamp` is the canonical event time field; producers that emit `ts`
  should normalize it to `timestamp`
- `event_type` is a coarse producer class such as `log` or `metric`
- `name` is the controlled telemetry taxonomy value, for example
  `tool.invoke`, `chat.message`, or `client.log.warning`
- `value` and `tags` preserve the existing external metric shape; collectors
  can also use richer `metrics` and `dimensions`
- `dimensions` are low-cardinality grouping fields
- `metrics` are numeric aggregates
- `meta` is for small producer-owned details; it must not contain raw message
  content by default

### Existing producer mapping

The external log collector shape is a source event, not a separate telemetry
schema. A collector can normalize it as follows:

| Source field | Normalized telemetry field |
| --- | --- |
| `event_type="log"` | `event_type="log"` |
| `origin` | `origin`, and often `source_component` |
| `timestamp` | `timestamp` |
| `timezone` | `timezone` |
| `level` | `name="client.log.<level>"`, `dimensions.level` |
| `message` | `meta.message` only when the collector policy allows log content |
| `args` | `meta.args` only when the collector policy allows log content |

For the documented external metric extension:

| Source field | Normalized telemetry field |
| --- | --- |
| `event_type="metric"` | `event_type="metric"` |
| `name` | `name` |
| `value` | `value` and/or `metrics.value` |
| `tags` | `tags` and/or `dimensions` |

Comm recording plus a telemetry sink should produce the same normalized
envelope when records are forwarded to telemetry:

| Comm fact | Normalized telemetry field |
| --- | --- |
| route/type/agent/step | `name`, `dimensions.route`, `dimensions.type`, `dimensions.agent`, `dimensions.step` |
| bundle id | `source_bundle` and `dimensions.bundle` |
| status/error | `status`, `error_kind`, `name` suffix where useful |
| timing/counters | `metrics` |

Do not introduce bundle-specific schemas such as `kdcube.usage.v1`. Usage
analytics is a taxonomy and storage concern over `kdcube.telemetry.v1` events.

## Ingestion Modes

```text
ChatCommunicator / runtime hooks / MCP / bundle SDK
  |
  | build or normalize kdcube.telemetry.v1 envelope
  | event_id is stable for retry
  v
telemetry emit(...)
  |
  +--> same KDCube REST operations ingest
  |
  +--> cross-KDCube public ingest
  |
  +--> optional local XADD telemetry stream
          |
          v
      telemetry consumer
          |
          | validate / normalize / batch
          v
      raw events store
          |
          | idempotent insert by event_id
          v
      rollup jobs
          |
          +--> daily/hourly aggregates
          |
          +--> Redis widget cache
          |
          v
      UI / chat tools / MCP read APIs
```

### Public REST ingest

Use this when a collector bundle receives events from another KDCube
environment.

Shape:

```text
POST /api/integrations/bundles/{tenant}/{project}/{collector_bundle}/public/ingest
```

Auth should be explicit, for example:

- proc-managed `public_auth={"mode":"header_secret", ...}`
- or `public_auth="bundle"` with bundle-owned signature/JWT validation

This is the recommended transport for cross-Kube telemetry collection when the
producer cannot share an internal stream with the collector.

### Operations REST ingest

Use this for same-KDCube calls where normal KDCube auth already exists.

Shape:

```text
POST /api/integrations/bundles/{tenant}/{project}/{collector_bundle}/operations/ingest
```

This is useful for explicit collector operations and user-visible actions that
are not already covered by comm recording and a configured telemetry sink.

### Redis Stream ingest

Use this behind a local ingest endpoint or platform hook when producer latency
must stay low.

Recommended key family:

```text
{tenant}:{project}:kdcube:telemetry:events:{shard}
```

Sharding key:

```text
hash(tenant, project, user_id or session_id or event_id) % N
```

Redis details:

- `XADD` can create streams lazily
- `XGROUP CREATE ... MKSTREAM` can create consumer groups lazily
- set an explicit retention or `MAXLEN` policy
- use `XAUTOCLAIM` for stale pending recovery

### Kafka ingest

Use Kafka when telemetry volume or multi-service fan-out outgrows Redis.

Recommended production policy:

- provision topics intentionally
- choose partition key by desired ordering scope
- keep the same event envelope
- keep DB-level idempotency by `event_id`

Kafka offsets do not replace event idempotency.

## Collector Implementation Shape

A collector bundle can start with:

```text
comm recording and event sinks / runtime hooks / MCP instrumentation
  -> telemetry emit(...)
  -> REST ingest endpoint
  -> raw event store
  -> incremental rollup
  -> Redis widget cache
  -> UI / chat / MCP read APIs
```

The collector can add a Redis Stream behind the ingest endpoint later:

```text
HTTP ingest
  |
  | auth / schema validation / privacy gate
  v
XADD telemetry stream
  |
  | consumer group
  v
telemetry consumer
  |
  | batch raw inserts
  | XACK after durable insert
  v
raw events store
  |
  | cron or incremental trigger
  v
rollup recompute
```

This avoids requiring a generic bundle listener before the longrun lifecycle
exists.

## Storage

The telemetry collector needs a durable raw event table plus rollups.

Minimum raw event table:

```sql
events(
  event_id text primary key,
  timestamp text not null,
  timezone text,
  tenant text not null,
  project text not null,
  user_id text,
  user_type text,
  session_id text,
  conversation_id text,
  turn_id text,
  event_type text not null,
  name text not null,
  source_kube text,
  source_component text,
  source_bundle text,
  origin text,
  value numeric,
  tags_json text not null,
  dimensions_json text not null,
  metrics_json text not null,
  status text,
  error_kind text,
  privacy_json text not null,
  meta_json text not null
)
```

Minimum rollups:

```sql
rollups_daily(user_id, day, name, source_bundle, metrics_json, primary key(...))
rollups_hourly(user_id, hour, name, source_bundle, metrics_json, primary key(...))
```

Rules:

- raw inserts are idempotent by `event_id`
- rollups are recomputable
- scheduled rollups must be safe to rerun
- Redis cache is only a cache, not the source of truth

SQLite is acceptable for local or low-volume deployments if write concurrency
is controlled. Postgres is the safer target for shared multi-writer collectors.

## Privacy

Default policy:

- do not store raw message text
- store message length, role, status, timing, token counts, and model/tool names
- topic classification must be opt-in or bounded to non-sensitive categories
- per-user data remains user-scoped unless an owner-facing aggregate explicitly
  anonymizes it
- `usage_forget(...)` must delete raw events and recompute rollups

## Relationship To Accounting

Accounting already records provider usage and cost-oriented service events.
Telemetry is broader:

- accounting answers "what resources were consumed?"
- telemetry answers "how was the platform used?"

Telemetry collectors should reuse accounting-derived token/cost facts where
available instead of duplicating provider usage extraction.

## Future Bundle Longrun Listener

```text
future platform event source
  |
  | XADD / publish telemetry envelope
  v
telemetry stream shard
  |
  | consumer group / lease / retry policy
  v
bundle @longrun telemetry-consumer
  |
  | open telemetry channel
  | batch events
  | durable write
  | ack after commit
  v
collector logic
```

A future bundle listener should be built on the proposed longrun lifecycle:

- [@longrun-README.md](../../sdk/bundle/build/design/@longrun-README.md)

A subscription is then a helper inside the longrun method, not the base
lifecycle primitive. That future transport must define:

- subscription declaration in the bundle manifest
- tenant/project/bundle routing
- auth and visibility
- stream group ownership
- retry and dead-letter policy
- backpressure behavior
- cooperative cancellation and props-change reconfiguration

Until that exists, telemetry collection should use explicit REST ingest and
scheduled rollups.
