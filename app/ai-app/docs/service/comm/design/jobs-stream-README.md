# Background Jobs Stream

This design adds a sibling ready-work queue for background jobs next to the chat task queue.

Redis Streams are used as a transport for jobs that are ready to run. They do not decide when work is due. Scheduling remains a producer concern: a cron job, bundle scheduler, admin action, or future service detects ready work and enqueues a job.

## Ownership

| Layer | Responsibility |
| --- | --- |
| Job producer | Detect due work, create any domain records, choose `work_kind`, `job_id`, `dedupe_key`, `metadata`, and `payload`. |
| Redis job stream | Persist ready jobs, dedupe submissions, expose consumer-group claiming and recovery. |
| Processor | Fairly poll chat work and background work, build a normal request context, and invoke the bundle job handler. |
| Bundle `@on_job` | Interpret `work_kind`, read bundle-owned records, execute the work, and update bundle-owned state. |

The platform must not understand bundle-specific job payloads. It only routes the envelope.

## Job Envelope

Top-level fields are platform-visible:

| Field | Purpose |
| --- | --- |
| `job_id` | Stable logical id used for locks, logs, and idempotency. |
| `work_kind` | Bundle-agreed operation name, for example `task.execution.due`. |
| `tenant`, `project`, `bundle_id` | Routing target. |
| `user_id`, `user_type`, `queue` | Runtime user context and fairness queue. |
| `dedupe_key` | Optional producer-chosen idempotency key. |
| `source` | Small audit object such as `scheduler`, `admin`, or `widget`. |
| `metadata` | Transport hints such as `conversation_id`, `turn_id`, `text`, `timezone`, `roles`. |
| `payload` | Bundle-owned domain payload, for example `{ "task_id": "...", "execution_id": "..." }`. |
| `created_at` | Unix timestamp for queue wait metrics. |

`metadata` and `payload` are JSON objects. The submitter and receiver can agree on their contents, but the processor should only use generic metadata needed to create the runtime context.

## Processing

1. A producer calls `RedisBackgroundJobStream.enqueue(...)`.
2. The stream writes to a tenant/project stream by queue, for example registered-user jobs and privileged jobs.
3. The processor loop polls chat work and background work in a simple round-robin.
4. For background work, the processor uses a Redis Stream consumer group and `XAUTOCLAIM` to recover idle pending jobs.
5. The processor builds a `ChatTaskPayload` with operation `__kdcube_on_job__`.
6. The proc runtime loads the target bundle and calls its async `@on_job` method.
7. The stream message is acknowledged only after the handler returns. Cancellation leaves the message pending for recovery.

`@on_job` handlers must be async. There is no sync fallback for job handlers.

## Task And Memo Pattern

The task-and-memo bundle uses this pattern:

| Producer | Job kind | Payload |
| --- | --- | --- |
| Due-task cron scan | `task.execution.due` | `task_id`, `execution_id`, `due_slot` |
| Widget/API run-now action | `task.execution.manual` | `task_id`, `execution_id` |

The producer creates a queued execution first. The queued execution is the user-visible source of truth. The job stream only transports the ready work.

The bundle `@on_job` handler then:

1. Validates `work_kind`.
2. Loads the task and execution by id.
3. Recreates a fresh task-run conversation context.
4. Runs the configured task execution agent.
5. Updates the execution journal, status, result, and artifacts.

## Idempotency

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

For manual run-now jobs, duplicate submission may be allowed because each click/request intentionally creates a new execution.
