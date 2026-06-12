---
id: repo:kdcube-ai-app/app/ai-app/docs/arch/proc/design/longrun-protection-for-cron-README.md
title: "Long-Run Protection For Cron Jobs"
summary: "Design note for bringing bundle @cron executions into the same useful-activity heartbeat and long-run protection model as processor queue tasks."
status: draft
tags: ["architecture", "processor", "cron", "scheduler", "longrun-protection", "heartbeat"]
updated_at: 2026-06-10
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/longrun-protection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/processor-arch-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-scheduled-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/conversation-event-lane-state-README.md
---
# Long-Run Protection For Cron Jobs

Bundle `@cron` jobs run inside proc but do not currently participate in the
same active-task metadata as queue-claimed chat tasks. The proc process
heartbeat still exists while cron jobs run, and `span=system|instance` jobs
also renew their scheduler Redis lock, but this is not the same as per-job
useful-activity tracking.

Cron jobs must be added to the long-run protection model before they are used
as durable lane consumers or other long-running platform work.

## Current Shape

Queue tasks are visible in proc heartbeat metadata through:

```text
processor.active_task_details[]
processor.max_active_task_idle_age_sec
processor.oldest_active_task_wall_age_sec
```

Each active queue task reports:

```text
task_id
bundle_id
queue_key
inflight_queue_key
started_execution
started_at
claimed_at
last_activity_at
last_activity_kind
activity_count
wall_age_sec
idle_age_sec
```

Cron jobs currently run through the bundle scheduler:

```text
@cron process span     -> scheduler-owned asyncio task
@cron instance/system  -> scheduler-owned call under Redis lock renewal
```

The scheduler lock renewal proves the lock holder is still renewing the job
lock. It does not prove the cron handler is still doing useful work inside the
Python task.

## Target

Every running cron invocation should have the same kind of visible activity
record as a queue task:

```text
job_id
bundle_id
job_alias
method_name
span
tenant
project
instance_id
process_id
started_at
last_activity_at
last_activity_kind
activity_count
wall_age_sec
idle_age_sec
lock_key
lock_renewed_at
```

This record should be included in proc heartbeat metadata, for example:

```text
processor.active_cron_jobs[]
processor.max_active_cron_idle_age_sec
processor.oldest_active_cron_wall_age_sec
```

## Useful Activity

Cron useful activity should be touched by:

```text
scheduler tick accepted
Redis lock acquired
bundle instance loaded
handler started
handler emits progress / comm event / longrun activity
handler awaits a known bounded operation
handler completes / fails / is cancelled
Redis lock renewed
```

The lock renewal is useful metadata but should not be the only activity signal.
A handler can keep the lock renewed while the actual cron work is idle or hung.

## Protection Policy

The scheduler should apply the same policy family as queue tasks:

```text
cron_max_wall_time_sec
cron_idle_timeout_sec
cron_watchdog_poll_interval_sec
```

When a cron job exceeds the policy:

```text
1. mark the cron invocation interrupted;
2. cancel the asyncio task;
3. release or let expire the scheduler lock;
4. emit structured scheduler/comm diagnostics;
5. keep enough metadata for operators to see which bundle/job was stopped.
```

For `span=system|instance`, cancellation must preserve the lock token rule:
only the owner that holds the token may delete the lock. If the token no longer
matches, the scheduler must not delete the lock.

## Relation To Event-Bus Lane State

If a cron job becomes a consumer for a conversation event lane, the cron
invocation must update the lane-local table explicitly:

```text
T.consumer.status
T.consumer.status_at
```

The proc heartbeat can then serve as the process/job safety source, while
`T.consumer.status_at` remains the lane-local useful-activity projection.

To correlate the lane to the heartbeat, the implementation must provide a
stable link such as:

```text
T.consumer.owner_ref -> {instance_id, process_id, job_id}
```

or heartbeat metadata indexed by:

```text
tenant + project + bundle_id + job_alias + job_id
```

Without this link, the event-bus lane cannot reliably determine which cron
heartbeat belongs to its consumer.

## Implementation Notes

This should be implemented in the scheduler runtime, not in individual bundles.
Bundles may expose cooperative progress points later, but the runtime must own:

```text
registration of active cron jobs
heartbeat metadata projection
idle/wall-time calculation
watchdog cancellation
lock-safe cleanup
diagnostic emission
```
