---
id: repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
title: "Background Job Streams"
summary: "Redis Stream contract for ready bundle-owned background work claimed by proc and dispatched to a bundle @on_job handler."
tags: ["service", "streams", "background-jobs", "redis", "proc", "on_job", "bundles"]
keywords: ["background job stream", "RedisBackgroundJobStream", "on_job", "ready work", "job envelope", "dedupe", "XAUTOCLAIM"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/telemetry-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-scheduled-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
---
# Background Job Streams

This stream family transports bundle-owned work that is already ready to run.

It is not a generic event bus, and it is not the conversation scheduler.

## Core Contract

Redis Streams are used as a transport for jobs that are ready to run. They do
not decide when work is due. Scheduling remains a producer concern: a cron job,
bundle scheduler, admin action, widget action, or internal service detects
ready work and enqueues a job.

The platform routes the envelope. The bundle owns the domain payload.

## Ownership

| Layer | Responsibility |
| --- | --- |
| Job producer | Detect due work, create any domain records, choose `work_kind`, `job_id`, `dedupe_key`, `metadata`, and `payload`. |
| Redis job stream | Persist ready jobs, dedupe submissions, expose consumer-group claiming and recovery. |
| Processor | Fairly poll chat work and background work, build a normal request context, and invoke the bundle job handler. |
| Bundle `@on_job` | Interpret `work_kind`, read bundle-owned records, execute the work, and update bundle-owned state. |

The platform must not understand bundle-specific job payloads.

## Job Envelope

Top-level fields are platform-visible:

| Field | Purpose |
| --- | --- |
| `job_id` | Stable logical id used for locks, logs, and idempotency. |
| `work_kind` | Bundle-agreed operation name, for example `task.execution.due`. |
| `tenant`, `project`, `bundle_id` | Routing target. |
| `user_id`, `queue_label` | Actor user id plus the scheduling/fairness queue label. `queue_label` is not authority and is not the economics subject. |
| `dedupe_key` | Optional producer-chosen idempotency key. |
| `source` | Small audit object such as `scheduler`, `admin`, or `widget`. |
| `identity_authority` | Optional Connection Hub authority projection snapshot for detached execution. |
| `metadata` | Transport hints such as `conversation_id`, `turn_id`, `text`, `timezone`, `roles`. |
| `payload` | Bundle-owned domain payload, for example `{ "task_id": "...", "execution_id": "..." }`. |
| `created_at` | Unix timestamp for queue wait metrics. |

`source`, `identity_authority`, `metadata`, and `payload` are JSON objects. The
submitter and receiver can agree on their contents, but the processor should only
use generic metadata needed to create the runtime context.

## Identity And Accounting

The job actor and the economics subject may be different.

Example: a Telegram actor may enqueue or own work as `telegram_100200300`, while
Connection Hub projection says platform user `02e5...` is the economics subject.
The job envelope must therefore carry `identity_authority` when the producer has
one. Proc restores it into:

- `ExternalEventPayload.user.identity_authority`
- `bundle_call_context.identity_authority`
- the base accounting envelope, where `user_id` is projected to the economics
  subject and metadata still records the actor

Bundle job handlers should keep domain ownership/provenance on the actor user
unless the product intentionally says otherwise. Paid work inside a job should
use shared economics helpers or inherit the bound accounting context. If a job
opens its own `with_accounting(...)` scope, pass the actor `user_id` and the
carried `identity_authority`; the accounting layer projects the billed
economics subject internally.

Do not reconstruct privileged/admin authority from the dispatch queue. Authority
comes from `identity_authority` and Connection Hub projection.

## Processing

```text
producer
  |
  | detect due work
  | create bundle-owned durable record
  | choose job_id / dedupe_key / work_kind
  v
RedisBackgroundJobStream.enqueue(...)
  |
  | XADD ready job envelope
  v
Redis Stream by tenant/project/queue_label
  |
  | XREADGROUP / XAUTOCLAIM
  v
proc worker
  |
  | acquire per-task lock
  | build ExternalEventPayload
  | bind bundle_call_context.kind=background_job
  v
bundle @on_job
  |
  | load bundle-owned record
  | execute idempotently
  | persist result/status/artifacts
  v
proc ACK
  |
  | XACK only after handler returns
  v
stream pending entry cleared
```

1. A producer calls `RedisBackgroundJobStream.enqueue(...)`.
2. The stream writes to a tenant/project stream by `queue_label`, for example registered-user jobs and privileged jobs.
3. The processor loop polls chat work and background work in a simple round-robin.
4. For background work, the processor uses a Redis Stream consumer group and `XAUTOCLAIM` to recover idle pending jobs.
5. The processor builds a `ExternalEventPayload` with operation `__kdcube_on_job__`
   and sets `bundle_call_context.kind=background_job`.
6. The proc runtime loads the target bundle and calls its async `@on_job` method.
7. The stream message is acknowledged only after the handler returns. Cancellation leaves the message pending for recovery.

`@on_job` handlers must be async. There is no sync fallback for job handlers.

## Chat Ingress Submit Helper

Some proc-hosted integrations need to accept a webhook/API request and submit a
normal chat turn without keeping the webhook open until the turn completes.
Those surfaces use `ChatIngressSubmitter.submit(...)`, not the background job
stream.

Example caller:

```python
result = await submit(
    session=session,
    request_context=request_context,
    message_data=message_data,
    message_text=processed_text,
    ingress=ingress,
    raw_attachments=raw_attachments,
)
result_payload = asdict(result)
```

That helper is a proc-local adapter around the canonical chat ingestion path:

```text
webhook / bundle API
  |
  v
ChatIngressSubmitter.submit(...)
  |
  v
process_chat_message(...)
  |
  v
normal chat queue / relay / conversation persistence
```

Use it when the work is a chat turn and should appear as normal assistant
conversation activity. Use `RedisBackgroundJobStream` when the work is a
bundle-owned background job handled by `@on_job`.

Identity note: the submit helper receives a normal `UserSession`. If that
session has `identity_authority`, `process_chat_message(...)` copies it into the
queued `ExternalEventPayload`, and the accounting envelope projects the
economics subject from it. Webhook resubmission should therefore authenticate or
claim the upstream request first, create the correct actor session, attach the
Connection Hub authority projection to that session, and then call the submit
helper. It should not enqueue a background job only to preserve identity.

## Bundle Job Dispatch

A bundle has one `@on_job` handler. The handler receives the background job
envelope and should dispatch by inspecting `work_kind` and `payload`.

Mixin-provided job handlers should be exposed as normal methods, not as
additional `@on_job` methods. A bundle that derives from such a mixin should call
the superclass dispatcher first:

```python
@on_job
async def on_job(self, **kwargs):
    handled = await super().handle_job(**kwargs)
    if handled.get("handled"):
        return handled

    job = kwargs.get("job") or {}
    work_kind = kwargs.get("work_kind") or job.get("work_kind")
    ...
```

This keeps the platform rule simple: there is exactly one decorated job entry
point per bundle, and the bundle owns dispatch for concrete job kinds.

## Task And Memo Pattern

The task-and-memo bundle uses this pattern:

| Producer | Job kind | Payload |
| --- | --- | --- |
| Due-task cron scan | `task.execution.due` | `task_id`, `execution_id`, `due_slot` |
| Widget/API run-now action | `task.execution.manual` | `task_id`, `execution_id` |

The producer creates a queued execution first. The queued execution is the
user-visible source of truth. The job stream only transports the ready work.

If the job then starts a nested tool/agent runtime, put task ids and other
agreed metadata in the reserved `bundle_call_context` instead of asking the
model to repeat those ids as tool arguments.

The bundle `@on_job` handler then:

1. Validates `work_kind`.
2. Loads the task and execution by id.
3. Recreates a fresh task-run conversation context.
4. Runs the configured task execution agent.
5. Updates the execution journal, status, result, and artifacts.

## Idempotency

```text
duplicate due scan
  |
  v
producer domain record check
  |
  | already exists
  v
do not enqueue duplicate

duplicate stream submission
  |
  v
dedupe_key SET NX
  |
  | already held
  v
return duplicate

handler retry after crash
  |
  v
@on_job loads existing execution/result record
  |
  v
continue or no-op according to bundle state
```

Use both layers:

| Layer | Role |
| --- | --- |
| Producer domain state | Prevent duplicate domain records for the same due slot or request. |
| Stream `dedupe_key` | Prevent duplicate ready-work submissions across processes. |
| Job handler | Treat retry as possible and update existing execution records by id. |

For scheduled tasks, a good dedupe key is:

```text
<bundle_id>:<user_id>:<task_id>:<due_slot>
```

For manual run-now jobs, duplicate submission may be allowed because each
click/request intentionally creates a new execution.

## What This Is Not

Background job streams should not be used as:

- telemetry ingestion for every usage event
- a generic bundle subscription system
- chat conversation scheduling
- live client event delivery

If the data is an observation such as `mcp.call`, `tool.invoke`, `llm.call`,
`comm.event`, or `client.log.warning`, use the telemetry stream contract
instead.
