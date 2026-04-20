---
id: ks:docs/ops/ecs/components/proc-README.md
title: "ECS Proc Component"
summary: "How chat-proc is deployed on ECS, what host/runtime prerequisites it has, and how it behaves during deploy, scale-in, and shutdown."
tags: ["ops", "ecs", "proc", "deployment", "shutdown", "task-protection"]
keywords: ["chat-proc", "ecs proc", "task scale-in protection", "docker-in-docker", "proc drain", "proc ec2"]
see_also:
  - ks:docs/arch/proc/processor-arch-README.md
  - ks:docs/arch/proc/longrun-protection-README.md
  - ks:docs/arch/ecs-service-README.md
---
# ECS Proc Component

This document describes the `chat-proc` service specifically as an ECS deployment unit.

It answers operational questions such as:

- what `proc` depends on from the host and task definition
- how busy proc tasks are protected during deploy / scale-in
- what still happens if ECS starts shutdown anyway
- how to reason about interrupted vs replayed work

---

## 1. What Proc Is

`chat-proc` is the execution side of chat processing.

On ECS it is a long-lived service task that:

- consumes queued chat turns from Redis
- runs bundle workflows
- emits relay/SSE events
- manages conversation state transitions
- may launch isolated code execution runtimes

For the current ECS/EC2 deployment, `chat-proc` also needs host capabilities that ordinary Fargate tasks do not provide for the hot path:

- host Docker socket access
- host-visible EFS mounts for bundle and exec workspace sharing
- host Docker registry auth for pulling `py-code-exec`

That is why the current cloud deployment model runs `chat-proc` on an EC2-backed ECS capacity provider.

---

## 2. Deployment Shape

Current intended ECS shape:

- one ECS service task family for `chat-proc`
- `awsvpc` networking
- EC2-backed capacity provider for proc
- task-level EFS mounts into the container:
  - `/bundles`
  - `/bundle-storage`
  - `/exec-workspace`
  - `/kdcube-storage`
- host-level bind mounts for Docker-in-Docker support:
  - `/var/run/docker.sock`
  - `/opt/kdcube/docker-auth` mounted into proc as `/home/appuser/.docker`

Important host bootstrap responsibilities:

- mount EFS access points on the EC2 host under `/opt/kdcube/efs/...`
- create Docker auth refresh script and systemd timer
- prewarm or at least authenticate the host Docker daemon for ECR
- start ECS only after the proc host prerequisites exist

If host bootstrap fails before those steps complete, proc may still appear as an ECS task, but Docker-based exec will fail because the host is not a valid proc runtime host.

---

## 3. Long-Running Turn Protection Model

`chat-proc` protects running bundle turns with multiple layers.

### App-level protections

- per-task Redis claim lock
- longer-lived started marker once the turn crosses the non-idempotent boundary
- graceful drain on shutdown: stop taking new work, wait for active tasks
- inflight reaper that distinguishes:
  - pre-start claim -> safe requeue
  - started turn -> interrupted, not replayed

### ECS-level protections

- ECS task scale-in protection while proc is actively executing work
- EC2 ASG / ECS capacity-provider managed termination protection to reduce host replacement while tasks are still running

These layers are complementary:

- app-level logic protects correctness and replay semantics
- ECS-level logic reduces the chance that a busy proc task is selected for stop in the first place

See:

- [processor-arch-README.md](../../../arch/proc/processor-arch-README.md)
- [longrun-protection-README.md](../../../arch/proc/longrun-protection-README.md)

---

## 4. How ECS Task Protection Works For Proc

When `ECS_AGENT_URI` is present, proc builds `EcsTaskScaleInProtection` in [task_protection.py](../../../../src/kdcube-ai-app/kdcube_ai_app/infra/aws/task_protection.py). Otherwise it uses a no-op helper.

At execution start, [processor.py](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py) wraps the turn in:

```python
async with self._task_scale_in_protection.hold(label=protection_label):
```

Behavior:

- first active worker process in the task enables ECS task scale-in protection through `${ECS_AGENT_URI}/task-protection/v1/state`
- additional worker processes only increment shared local claim state
- when the last active worker finishes, proc clears task protection

Implementation details:

- shared lock/state files under `/tmp`
- claims are counted per PID
- stale PID claims are swept opportunistically
- protection expiry is derived from `CHAT_TASK_TIMEOUT_SEC`, with extra headroom, then capped by ECS limits

This is task-wide protection, not per-request protection. If a proc ECS task is busy with any active turn, the task stays protected until it becomes idle again.

What it protects against:

- ECS service deployments replacing tasks
- ECS service scale-in

What it does not protect against:

- host loss
- explicit force stop
- bootstrap failure before proc starts
- protection expiry if work exceeds the protection window and nothing renews it

---

## 5. What Happens During Upgrade Or Downscale

### Case A: proc task is idle

ECS may replace or stop it immediately.

That is fine because:

- no bundle turn is running
- no inflight request needs protection

### Case B: proc task is busy and task protection is active

Desired behavior:

- ECS should not choose that task for service deployment scale-in while it remains protected
- the task keeps processing until active work completes
- after the last active turn finishes, proc clears protection and the task becomes eligible for replacement

### Case C: shutdown has already started

Once ECS actually starts stopping the container, task protection is no longer the main control plane. The task is already being terminated.

At that point proc:

- marks itself draining
- stops accepting new work
- waits for active tasks via `processor.stop_processing()`
- relies on the configured container stop window

The shutdown budget is derived from:

- `PROC_CONTAINER_STOP_TIMEOUT_SEC`
- optional `PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC`

See [ecs-service-README.md](../../../arch/ecs-service-README.md) for the exact timeout interaction.

---

## 6. Correctness Semantics During Failure

Proc intentionally does not auto-replay started turns.

Rule:

- claimed but not started -> recoverable, may be requeued
- started -> non-idempotent, interrupt instead of replay

Why:

- the client may already have seen partial SSE output
- workflows and tools may have already caused side effects
- replaying the same started turn could duplicate or conflict with prior effects

Operational consequence:

- if a proc task is lost after a started turn begins, the user sees an interrupted turn
- that is safer than silently replaying the same request

---

## 7. Host Prerequisites For Docker Exec

For the EC2-backed proc deployment, a healthy host must have all of these:

- EFS mounts under `/opt/kdcube/efs/...`
- Docker auth under `/opt/kdcube/docker-auth/config.json`
- Docker socket available to the proc task
- proc container env mapping host-visible paths:
  - `HOST_BUNDLES_PATH`
  - `HOST_MANAGED_BUNDLES_PATH`
  - `HOST_EXEC_WORKSPACE_PATH`
  - `HOST_BUNDLE_STORAGE_PATH`

If these are missing, typical symptoms are:

- `react_tools.py` or other bundle files missing in exec container
- `main.py not found in workdir`
- `no basic auth credentials` on `py-code-exec` pull

These are deployment/bootstrap failures, not normal processor semantics.

---

## 8. Operational Signals To Watch

Useful proc logs:

- `Enabled ECS task scale-in protection for busy proc task`
- `Disabled ECS task scale-in protection after proc became idle`
- `Failed to enable ECS task scale-in protection`
- `Starting processor drain: metadata=...`
- `Requeued stale pre-start inflight task ...`
- `Marked stale started task ... as interrupted`

Useful host checks:

```bash
findmnt /opt/kdcube/efs/bundles
findmnt /opt/kdcube/efs/exec-workspace
ls -la /opt/kdcube/docker-auth
systemctl status kdcube-refresh-docker-auth.timer --no-pager
```

Useful proc-container checks:

```bash
echo "$DOCKER_CONFIG"
ls -la /home/appuser/.docker
ls -la /bundles
ls -la /exec-workspace
```

---

## 9. Summary

`chat-proc` on ECS is protected by both processor-level correctness rules and ECS-level deployment protections.

The intended behavior is:

- busy proc tasks should avoid selection for shutdown during deployment/scale-in
- if shutdown still begins, proc drains within the configured stop window
- already-started turns are interrupted rather than replayed

That gives the platform a conservative correctness model for long-running bundle work while still letting ECS replace and scale proc replicas safely.
