---
id: ks:docs/service/streams/README.md
title: "Service Streams"
summary: "Map of KDCube stream families: background jobs, telemetry, conversation scheduling, and relay/pubsub, with ownership and reliability boundaries."
tags: ["service", "streams", "redis", "kafka", "telemetry", "background-jobs", "scheduler"]
keywords: ["stream families", "background job stream", "telemetry stream", "conversation scheduler stream", "redis streams", "kafka mapping", "bundle event listener"]
see_also:
  - ks:docs/service/streams/background-jobs-README.md
  - ks:docs/service/streams/telemetry-README.md
  - ks:docs/service/streams/conversation-scheduler-README.md
  - ks:docs/service/external-log-collector/frontend-events-design.md
  - ks:docs/sdk/bundle/build/design/@longrun-README.md
  - ks:docs/arch/proc/processor-arch-README.md
---
# Service Streams

KDCube uses the word "stream" for several different mechanisms. They are not
interchangeable.

This page separates the stream families by product responsibility, ownership,
and reliability model.

## Stream Families

| Family | Primary purpose | Producer | Consumer | Current status |
| --- | --- | --- | --- | --- |
| Background jobs | Execute ready bundle-owned work fairly through proc | Bundle cron, widget/API action, internal service | Proc, then bundle `@on_job` | Implemented |
| Telemetry | Collect observations about platform and bundle usage | Comm promotion, runtime hooks, SDK emitters, external client log collector, MCP/API instrumentation | Telemetry collector bundle | Proposed |
| Conversation scheduler | Schedule chat turns by conversation ownership | Chat ingress | Proc conversation owner loop | Target design, not implemented as proc backend |
| Relay/pubsub | Fan out live chat events to connected clients | Proc/bundle communicator | Ingress SSE/Socket.IO holders | Implemented, non-durable |

The first three can all use Redis Streams or Kafka-like logs, but the stream
primitive does not define the application semantics. Each family has its own
contract.

## Decision Rule

Use the stream family by the work being modeled:

- use **background jobs** when a bundle owns work that should execute later via
  `@on_job`
- use **telemetry** when recording facts that happened, such as chat message
  metadata, MCP calls, tool invocations, model usage, latency, comm events, or
  client log events
- use **conversation scheduler** streams when deciding which proc worker owns
  and executes a conversation
- use **relay/pubsub** only for live client delivery; do not treat it as durable
  storage

## Important Boundaries

### Background jobs are ready work

A background job stream item says:

```text
run this bundle job
```

It is acknowledged only after the bundle `@on_job` handler returns. The handler
must be idempotent because retries are possible.

### Telemetry events are observations

A telemetry event says:

```text
this happened
```

It should be accepted quickly, deduped by `event_id`, stored as a raw event, and
aggregated later. It should not execute arbitrary bundle work per event on the
processor critical path.

### Conversation scheduler streams are ownership signals

A conversation scheduler wake-up says:

```text
this conversation has pending mailbox work and needs an owner
```

The hard part is not `XREADGROUP`. The hard part is leases, ordered mailbox
delivery, fairness, and started-turn recovery.

## Bundle Listener Status

There is currently no generic bundle lifecycle surface such as:

```python
@on_event("chat.message")
async def handle_event(...):
    ...
```

Current bundle inbound surfaces are request/job oriented:

- chat turn
- `@api(...)`
- `@ui_widget(...)`
- `@mcp(...)`
- `@cron(...)`
- `@on_job`

So a telemetry collector bundle should start with explicit ingestion endpoints
and scheduled rollups. A future platform-level `@on_event` transport can be
introduced later as a helper on top of a broader `@longrun` lifecycle without
confusing it with background jobs or the conversation scheduler.

Design note:

- [Bundle Longrun Design](../../sdk/bundle/build/design/@longrun-README.md)

## Redis Streams And Kafka

Redis Streams and Kafka can both support these families, but provisioning and
recovery differ.

Redis Streams:

- streams can be created lazily with `XADD`
- consumer groups can be created lazily with `XGROUP CREATE ... MKSTREAM`
- retention must be controlled with `MAXLEN` or trimming policy
- retry/repair uses pending lists, `XCLAIM`, or `XAUTOCLAIM`

Kafka:

- topics should be provisioned intentionally in production
- partitions define ordering scope
- consumer group offsets are not a replacement for application idempotency
- replay policy still belongs to the stream family contract

## Reader Map

- Background jobs: [background-jobs-README.md](background-jobs-README.md)
- Telemetry collection: [telemetry-README.md](telemetry-README.md)
- Conversation scheduler: [conversation-scheduler-README.md](conversation-scheduler-README.md)
- Processor architecture: [../../arch/proc/processor-arch-README.md](../../arch/proc/processor-arch-README.md)
