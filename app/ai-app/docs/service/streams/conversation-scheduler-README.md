---
id: repo:kdcube-ai-app/app/ai-app/docs/service/streams/conversation-scheduler-README.md
title: "Conversation Scheduler Streams"
summary: "Service-level boundary for the target conversation-native processor scheduler using mailbox streams, wake streams, leases, owner loops, and started-turn recovery."
status: proposal
tags: ["service", "streams", "conversation-scheduler", "proc", "redis", "kafka", "leases", "followup", "steer"]
keywords: ["conversation scheduler stream", "conversation mailbox", "wake stream", "conversation lease", "owner loop", "started marker", "CHAT_SCHEDULER_BACKEND"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/processor-arch-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/design/conversation-scheduler-streams-README.md
---
# Conversation Scheduler Streams

Conversation scheduler streams are for deciding which proc worker owns and
executes a conversation.

They are not background jobs and they are not telemetry.

## Current Status

The shipped processor still uses the legacy list-based task scheduler for
normal chat turns.

The `CHAT_SCHEDULER_BACKEND=conversation_streams` option is reserved, but it
fails fast at startup because the mailbox/lease/owner-loop scheduler is not
wired into proc yet.

The detailed target design lives in:

- [../../arch/proc/design/conversation-scheduler-streams-README.md](../../arch/proc/design/conversation-scheduler-streams-README.md)

This page is the service-level boundary explaining how that stream family is
different from the others.

## Purpose

A conversation scheduler stream item is not "run this job".

It means:

```text
this conversation has pending mailbox work and may need an owner
```

The scheduler then needs to decide:

- whether a worker can acquire the conversation lease
- which mailbox item is next
- whether the current turn has crossed the started boundary
- whether live followup/steer can be consumed by the active runtime
- when to release or reschedule ownership for fairness

## Target Flow

```text
chat ingress
  |
  | classify message: regular / followup / steer
  | append ordered mailbox item
  v
conversation mailbox
  |
  | set scheduled marker if absent
  v
shard wake stream
  |
  | XREADGROUP by proc workers
  v
proc worker
  |
  | acquire conversation lease
  | if lease not acquired, ACK/collapse wake-up
  v
conversation owner loop
  |
  | read mailbox head
  | mark turn started before side effects
  | execute bundle turn
  |
  +--> reactive runtime consumes live followup/steer
  |
  +--> non-reactive runtime leaves mailbox items pending
  |
  v
completion boundary
  |
  | mailbox empty
  | clear scheduled marker
  | release lease
  v
conversation idle
```

If the fairness budget is exhausted while mailbox work remains:

```text
owner loop
  |
  | fairness boundary reached
  v
emit new wake-up
  |
  | keep mailbox state
  | release lease
  v
another proc worker may become owner
```

## Target Primitives

### Conversation mailbox

All accepted messages for one conversation land in ordered shared storage.

Example key:

```text
{tenant}:{project}:kdcube:chat:bundle:{bundle_id}:user:{user_id}:conv:{conversation_id}:mailbox
```

The mailbox carries regular turns, followups, and steers.

### Wake stream

Workers consume small wake-up events from shard streams.

Example key:

```text
{tenant}:{project}:kdcube:chat:bundle:{bundle_id}:shard:{shard_id}:wake
```

The wake event says a conversation may need attention. It should not carry the
full task payload.

### Conversation lease

At most one proc worker owns a conversation at a time.

Example key:

```text
{tenant}:{project}:kdcube:chat:bundle:{bundle_id}:user:{user_id}:conv:{conversation_id}:lease
```

The lease is renewable and bounded.

### Scheduled marker

A dedupe marker prevents unbounded duplicate wake-ups while the conversation is
already known to the scheduler.

### Started-turn marker

The existing recovery rule remains:

- pre-start work may be replayed
- started turns are interrupted, not auto-replayed

Streams do not change that product rule.

## Redis Streams Role

```text
mailbox stream
  |
  | ordered append per conversation
  v
owner loop reads next mailbox item

wake stream
  |
  | consumer group distributes conversations needing owners
  v
proc worker attempts lease

lease key / state
  |
  | renewable ownership
  v
at most one active owner per conversation
```

Redis Streams are useful for:

- ordered mailbox append/read
- wake-up streams
- consumer group ownership
- pending inspection
- repair with `XCLAIM` / `XAUTOCLAIM`

But Redis Streams do not by themselves provide:

- conversation ownership
- fairness boundaries
- started-turn replay policy
- runtime continuation semantics

Those are scheduler semantics.

## Kafka Mapping

The same model can map to Kafka:

- mailbox topic partitioned by conversation id
- wake topic partitioned by scheduler shard
- explicit lease state in Redis/Postgres or a compacted topic
- application-level idempotency and started-turn state

Kafka consumer offsets do not replace the started-turn recovery rule.

## Relationship To Other Stream Families

| Family | Difference |
| --- | --- |
| Background jobs | Background jobs call bundle `@on_job`; conversation scheduler streams own chat turn execution order and worker ownership. |
| Telemetry | Telemetry records what happened; scheduler streams decide what should execute next. |
| Relay/pubsub | Relay delivers live client events; scheduler streams are durable control state for proc execution. |

## Migration Direction

The processor architecture doc defines the migration phases:

```text
current
  global lane queue -> one task -> one turn

intermediate
  global lane queue + continuation mailbox + promotion

target
  conversation mailbox -> shard wake stream -> lease owner
    -> owner loop -> release or reschedule
```

1. keep current workflow continuation abstraction
2. add scheduler backend abstraction
3. introduce wake streams and leases
4. move non-reactive continuation processing into the owner loop
5. enable live continuation on the new scheduler
6. retire the old global-lane queue for chat turns

Until that work is complete, keep `CHAT_SCHEDULER_BACKEND=legacy_lists`.
