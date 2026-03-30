---
id: ks:docs/arch/ecs-service-README.md
title: "ECS Service Timeouts"
summary: "How ECS stopTimeout, Uvicorn graceful shutdown, proc task timeout, and Redis leases interact during scale-down and deployment."
tags: ["arch", "ecs", "fargate", "timeouts", "proc", "ingress", "shutdown"]
keywords: ["ecs stopTimeout", "uvicorn graceful shutdown", "proc drain", "inflight task", "fargate termination", "chat task timeout"]
see_also:
  - ks:docs/arch/proc/processor-arch-README.md
  - ks:docs/clients/frontend-awareness-on-service-state-README.md
  - ks:docs/service/maintenance/requests-monitoring-README.md
---
# ECS Service Timeouts

This document explains the timeout model for `chat-ingress` and `chat-proc` on ECS/Fargate and answers the practical question:

> If ECS decides to stop a `proc` replica during deployment or scale-down, how much time does an inflight bundle turn really have to finish?

The short answer is:

- during normal steady-state operation, a proc turn may run up to the configured proc task timeout
- once ECS starts terminating the replica, the remaining graceful time is much smaller
- with the current configuration, a proc turn has at most about **110 seconds after `SIGTERM`** to finish gracefully

That is not a Lambda-style request timeout.
It is a **termination-time drain budget**.

Important distinction:

- if the proc task has **already received shutdown**, the remaining time is bounded by the ECS stop window
- if you want a busy proc task to **keep running and avoid shutdown in the first place**, the relevant mechanism is **ECS task scale-in protection**

---

## 1. Current Timeout Matrix

### Ingress

| Timeout | Current value | Source | What it controls |
| --- | --- | --- | --- |
| ECS container `stopTimeout` | `60s` | `deployment/ecs/terraform/modules/ecs/task_chat_ingress.tf` | How long ECS waits after sending stop to the ingress container before force-killing it. |
| Uvicorn `timeout_graceful_shutdown` | `15s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py` | How long Uvicorn lets ingress workers finish shutdown before it gives up. |
| Uvicorn `timeout_keep_alive` | `45s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py` | Idle HTTP keep-alive timeout during normal serving. Not a shutdown budget. |
| Uvicorn `timeout_worker_healthcheck` | default `60s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py` | Worker startup healthcheck timeout. Startup only, not shutdown. |

### Proc

| Timeout | Current value | Source | What it controls |
| --- | --- | --- | --- |
| ECS container `stopTimeout` | `120s` | `deployment/ecs/terraform/modules/ecs/task_chat_proc.tf` | How long ECS waits after sending stop to the proc container before force-killing it. |
| Uvicorn `timeout_graceful_shutdown` | `110s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py` | How long Uvicorn lets proc workers finish shutdown and drain inflight work before it gives up. |
| Proc task timeout (`CHAT_TASK_TIMEOUT_SEC`) | `600s` effective | `task_chat_proc.tf` + `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` | Maximum runtime of one bundle turn during normal processing. |
| Proc lock TTL | `300s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` | Redis lease for an inflight task. Used for ownership/recovery, not for normal runtime limits. |
| Proc lock renew interval | `60s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` | How often the inflight lock is extended while work is alive. |
| Queue block timeout | `0.1s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` | How long queue polling waits per lane. Used so drain can stop taking new work quickly. |
| Queue call timeout | `2.0s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` | Client-side timeout around Redis queue claim calls. Recovery-related, not turn runtime. |
| Uvicorn `timeout_worker_healthcheck` | default `60s` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py` | Worker startup healthcheck timeout. Startup only, not shutdown. |

Important nuance:

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/resolvers.py` passes `task_timeout_sec=900` to the processor constructor
- but `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py` then overrides from `CHAT_TASK_TIMEOUT_SEC`
- ECS currently sets `CHAT_TASK_TIMEOUT_SEC=600`
- so the effective proc turn timeout in production is currently **600 seconds**

---

## 2. The Three Different Kinds Of Timeouts

These timeouts are easy to mix up, but they control different things.

### 2.1 Normal processing timeout

This is the business/runtime budget for one proc turn:

- current effective value: `600s`
- controlled by `CHAT_TASK_TIMEOUT_SEC`

If the task is healthy and ECS is not stopping the replica, the bundle may run up to this limit.

### 2.2 App graceful-shutdown timeout

This is how long Uvicorn gives the process to shut down cleanly after stop begins:

- ingress: `15s`
- proc: `110s`

For proc, this is the budget for:

- stop dequeuing new tasks
- keep existing inflight tasks running
- let inflight tasks finish if they can finish in time

### 2.3 ECS termination timeout

This is how long ECS waits before force-killing the container after stop begins:

- ingress: `60s`
- proc: `120s`

This is the outer hard budget.
If the container is still alive when this budget expires, ECS may terminate it forcefully.

---

## 3. Timeouts That Do Not Limit Inflight Proc Turn Completion

These values are easy to confuse with the real turn-finish budget, but they do not define how long an inflight proc turn may continue after shutdown starts.

### Startup-only / health-only values

- Uvicorn `timeout_worker_healthcheck`
- container healthcheck `interval`
- container healthcheck `timeout`
- container healthcheck `retries`
- container healthcheck `startPeriod`

These affect:

- worker startup
- readiness / health evaluation
- dependency gating between containers

They do not define how long a running proc turn may continue during shutdown.

### Connection-lifecycle values

- Uvicorn `timeout_keep_alive`
- SSE keepalive intervals
- Redis queue poll timeouts

These affect:

- idle HTTP connections
- stream keepalive behavior
- queue polling responsiveness

They are not the main drain budget for an inflight proc turn.

---

## 4. What Actually Happens On Proc Shutdown

When ECS scales down a proc replica or replaces it during deployment, the practical flow is:

1. ECS starts stopping the proc container.
2. The proc process receives termination and enters drain mode.
3. Proc stops taking new messages from Redis.
4. Existing inflight bundle turns are allowed to continue.
5. Uvicorn waits up to `timeout_graceful_shutdown`.
6. ECS waits up to container `stopTimeout`.
7. Whichever hard boundary is reached first ends the graceful period.

With the current configuration:

- Uvicorn graceful budget: `110s`
- ECS hard stop budget: `120s`

So the proc process has a graceful drain window of about **110 seconds**, with about **10 seconds of spare margin** before ECS reaches the outer container stop window.

---

## 5. How Much Time Does An Inflight Proc Turn Have?

This is the key question.

The remaining time for a currently running proc turn after shutdown starts is approximately:

```text
remaining_grace =
  min(
    uvicorn_graceful_shutdown_budget,
    ecs_container_stop_timeout,
    remaining_proc_task_timeout
  )
```

With the current values:

```text
remaining_grace =
  min(110s, 120s, remaining_of_600s_turn_budget)
```

Examples:

### Example A: short-running turn

- the turn has been running for `20s`
- shutdown starts now
- remaining task budget is `580s`

Result:

```text
min(110, 120, 580) = 110s
```

So this inflight turn has at most about **110 more seconds** to finish gracefully.

### Example B: already long-running turn

- the turn has already been running for `560s`
- shutdown starts now
- remaining task budget is `40s`

Result:

```text
min(110, 120, 40) = 40s
```

So this inflight turn has only **40 more seconds** before its own proc task timeout would fire anyway.

### Practical conclusion

After ECS has decided to stop that proc replica, the maximum additional graceful time is **not 600 seconds**.
It is currently about **110 seconds**.

---

## 6. What This Does And Does Not Mean

### What it means

- Proc can run long turns during normal operation.
- Proc cannot guarantee completion of an arbitrarily long inflight turn once ECS is terminating that replica.
- A deployment or scale-down event introduces a hard graceful-drain ceiling.

### What it does not mean

- It does **not** mean proc is limited to 110 seconds in general.
- It does **not** mean ECS behaves like Lambda for every request.
- It means only that **once the replica is being terminated**, the remaining time is bounded by the termination budgets above.

That distinction is important:

- steady state runtime budget: `600s`
- post-termination grace budget: about `110s`

---

## 7. Why We Lowered Proc Uvicorn Graceful Timeout Below ECS stopTimeout

Earlier, proc used:

```text
proc Uvicorn graceful shutdown = 120s
proc ECS stopTimeout           = 120s
```

That left no margin between:

- the app finishing its own graceful shutdown
- ECS reaching the task-level hard stop window

Now proc uses:

```text
proc Uvicorn graceful shutdown = 110s
proc ECS stopTimeout           = 120s
```

This is better because:

- the app has a large graceful drain window
- Uvicorn should finish its own shutdown path before ECS reaches the hard stop point
- there is less risk that app-level timeout and ECS hard stop happen at the same moment

---

## 8. Why Ingress Is Different

Ingress is not supposed to wait for long bundle execution.
It mostly needs to:

- stop accepting new traffic
- close SSE/Socket.IO/relay listeners
- stop background schedulers

So ingress uses:

- `timeout_graceful_shutdown = 15s`
- ECS `stopTimeout = 60s`

That is reasonable because ingress should shut down much faster than proc.

---

## 9. The Real Product Limitation

If you want a hard guarantee that an inflight turn can survive deployment/scale-down even when it still needs several minutes to finish, then the current ECS service model has a real limitation.

Why:

- the proc replica is still a service task
- service tasks are expected to stop when ECS replaces or scales them down
- once stop begins, graceful completion time is bounded

So the current model is good for:

- long turns during stable operation
- graceful drain of many ordinary turns
- surfacing interruption cleanly when stop happens mid-turn

But it is not a perfect fit for:

- guaranteed uninterrupted multi-minute execution across service replacement

---

## 10. What To Do If We Need Longer Guaranteed Completion

There are only a few real options.

### Option A: accept interruption semantics

Current platform behavior already does this:

- started turns are not auto-replayed
- clients receive interruption signals
- the user may retry manually

This is acceptable only if interruption is a valid product behavior.

### Option B: reduce how often busy proc replicas are stopped

This can improve real-world behavior, but it does not remove the hard ceiling.

Examples:

- deploy less often
- scale down conservatively
- avoid terminating busy workers when possible

Useful operationally, but not a full guarantee.

### Option C: use ECS task scale-in protection for busy proc tasks

This is the most important ECS-native mechanism if the desired behavior is:

- "while proc has inflight work, do not let ECS terminate this replica for scale-down or deployment"

AWS supports **task scale-in protection** for service tasks.
Per AWS docs:

- a protected task is not terminated by **Service Auto Scaling or deployments**
- protection can be set for from `1` minute up to `48` hours
- default protection duration is `2` hours if not specified

This changes the problem fundamentally:

- without protection:
  - ECS may choose a busy proc task for termination
  - once `SIGTERM` is sent, only the `110s/120s` drain window remains
- with protection:
  - busy proc can mark itself protected when inflight work starts
  - ECS should avoid terminating that task while protection is active
  - the turn can continue to use its normal runtime budget instead of entering termination drain
  - when proc becomes idle again, protection can be removed

This is the right answer if the desired behavior is:

- "long-running inflight proc tasks should keep running during scale-down/update instead of being interrupted"

This is **not** the same as extending post-`SIGTERM` runtime beyond 120 seconds.
Task protection helps by preventing `SIGTERM` from being sent to a busy task in the first place.

Operationally, this would look like:

1. proc starts the first inflight task
2. proc enables ECS task protection for its own service task
3. while proc remains busy, it refreshes protection before expiration
4. when proc becomes idle, it clears protection and becomes eligible for replacement

Current repo status:

- I did **not** find existing IAM/policy wiring for `ecs:UpdateTaskProtection` / `ecs:GetTaskProtection`
- so this is a recommended next implementation step, not something already active

### Option D: move truly long execution out of the service replica lifecycle

If a turn must survive proc replica replacement, then the execution boundary should not be the same thing as the ECS service replica that is freely scaled and replaced.

Typical patterns:

- standalone ECS tasks launched for long-running jobs
- separate execution workers/jobs with durable state
- checkpoint/resume workflow model
- external job orchestration layer

This is the architectural answer if multi-minute uninterrupted completion is a hard requirement.

---

## 11. Bottom Line

Current proc timing semantics are:

- normal turn runtime budget: **600s**
- graceful drain budget after ECS stop begins: about **110s**
- ECS outer hard stop budget: **120s**

So:

- long turns are supported during steady state
- long turns are **not guaranteed** to finish once ECS is terminating that replica
- if you want busy proc replicas to avoid termination during scale-down/deployment, add ECS task scale-in protection
- if you need stronger guarantees even beyond that, the execution model must move beyond “service replica owns the whole turn”

---

## 12. External References

Useful AWS references:

- ECS container stop timeout: <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html>
- ECS task shutdown / `SIGTERM` then `SIGKILL`: <https://aws.amazon.com/blogs/containers/graceful-shutdowns-with-ecs/>
- EFS on ECS/Fargate and `aws-fargate-supervisor`: <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/efs-volumes.html>
- ECS task scale-in protection: <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-scale-in-protection.html>
- ECS task protection endpoint: <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-scale-in-protection-endpoint.html>
