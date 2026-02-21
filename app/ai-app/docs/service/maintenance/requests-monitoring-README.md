# Requests Monitoring Cheat Sheet

Quick reference for profiling slow or delayed chat requests using admin UIs, Redis browser, and server logs.

## What “slow start” usually means
A request is accepted by `/sse/chat` or `/socket.io` but progress events (`chat_start`, `chat_step`, `chat_delta`) arrive late.

Primary places to measure latency:
1. Ingress → enqueue (SSE/WS handler → Redis enqueue)
2. Queue wait (Redis list → processor acquire)
3. Processor start → first relay event (SSE stream / Socket.IO client delivery)

## Super fast monitoring flow (60s)
1. Open Control Plane Monitoring Dashboard and refresh.
2. Check `Total Queue`, `Queue Analytics` avg wait, and `chat_rest` healthy count.
3. If avg wait is high, check Redis queues and heartbeats.
4. If avg wait is low but UI is quiet, check SSE stream delivery logs.

## Key files and UIs
- Monitoring UI: `kdcube_ai_app/apps/chat/api/monitoring/ControlPlaneMonitoringDashboard.tsx`
- Redis Browser UI: `kdcube_ai_app/apps/chat/api/control_plane/RedisBrowser.tsx`
- SSE entrypoint: `kdcube_ai_app/apps/chat/api/sse/chat.py`
- Ingress + enqueue: `kdcube_ai_app/apps/chat/api/ingress/chat_core.py`
- Processor: `kdcube_ai_app/apps/chat/processor.py`

## Monitoring Dashboard (Control Plane)
Open the Control Plane Monitoring Dashboard and check:
1. System Summary
2. Queues (anonymous/registered/privileged)
3. Queue Analytics (avg wait, throughput, utilization)
4. Capacity Transparency (actual healthy processes vs configured)

Interpreting signals:
- High queue size + high avg wait → queue pressure or too few healthy processes.
- Low queue size + high avg wait → delivery/stream issue or processor starvation.
- Low healthy processes right after restart → enqueue may reject or queue wait will spike.

## Redis Browser (Control Plane)
Use the quick prefix buttons to inspect keys fast:
1. Queues: `<tenant>:<project>:kdcube:chat:prompt:queue` (list)
2. Locks: `<tenant>:<project>:kdcube:lock` (string)
3. Process heartbeats: `<tenant>:<project>:kdcube:heartbeat:process` (string JSON)
4. Instance heartbeats: `<tenant>:<project>:kdcube:heartbeat:instance` (string JSON)
5. Capacity: `<tenant>:<project>:kdcube:system:capacity` (string)
6. Bundles: `kdcube:config:bundles:` (hash/string)

What to look for:
- Queue lists growing but no dequeue → processor not running or blocked.
- Locks with old TTLs → stuck tasks or lock renew failure.
- Missing heartbeats → unhealthy or dead process.

## Logs to watch (new timing signals)
Look for these log lines:
1. Ingress enqueue timing
   - "enqueue_chat_task_atomic result task_id=... enqueue_ms=... ingress_to_enqueue_ms=..."
2. Queue wait timing
   - "Process <pid> acquired task <task_id> (...) queue_wait_ms=..."
3. Processor start timing
   - "Starting task <task_id> queue_wait_ms=... current_load=..."

How to interpret:
- If `enqueue_ms` is high: Redis or Lua script is slow (possible KEYS scan pressure).
- If `queue_wait_ms` is high: backlog or not enough healthy processes.
- If `queue_wait_ms` is low but UI is quiet: SSE/Socket stream delivery issue.

## Log filters (copy/paste)
Use these patterns with your log stream or files:
1. Enqueue and queue wait timing
   - `enqueue_chat_task_atomic result|acquired task|Starting task`
2. SSE delivery issues
   - `SSEHub|sse_stream|no recipients found|DIRECT SEND|BROADCAST`
3. Gateway rejections and pressure
   - `gateway|backpressure|queue.enqueue_rejected|circuit_breaker`

Examples:
1. `rg -n "enqueue_chat_task_atomic result|acquired task|Starting task" /path/to/logs/*.log`
2. `rg -n "SSEHub|sse_stream|no recipients found" /path/to/logs/*.log`
3. `rg -n "queue.enqueue_rejected|backpressure|circuit_breaker" /path/to/logs/*.log`

## Quick triage checklist
1. Verify processor is running and heartbeats are present.
2. Check `queue_wait_ms` logs and Redis queue size.
3. Confirm SSE stream uses the same `stream_id` as `/sse/chat`.
4. Check for rejected enqueues (queue pressure, no healthy processes).

## Useful endpoints
Use these for fast diagnostics:
1. `GET /monitoring/system`
2. `GET /admin/circuit-breakers`
3. `POST /admin/circuit-breakers/{name}/reset`

Note: Responses include queue stats, capacity context, and throttling data.

## Notes on common root causes
1. Stream ID mismatch
   - `/sse/stream?stream_id=...` and `/sse/chat?stream_id=...` must match.
2. Processor capacity leak on invalid payloads
   - Fixed in `kdcube_ai_app/apps/chat/processor.py` (load is decremented on invalid payload).
3. Redis KEYS in enqueue Lua
   - Can block Redis under large keyspace; watch `enqueue_ms` spikes.

## Expected heartbeat behavior after restart
Defaults:
1. Heartbeats are emitted every ~10 seconds.
2. A process is considered stale after ~45 seconds.

What to expect:
1. `chat_rest` healthy count should become `>= 1` within 10 to 30 seconds after restart.
2. If `chat_rest` stays at 0 after ~45 seconds, the processor is not running or Redis is unhealthy.

## Minimal reproduction hints
1. Restart server, immediately send a message and watch `queue_wait_ms`.
2. Send a second message right after “idle” status is emitted.
3. Compare SSE stream logs vs processor acquire logs.

## Server-side load testing (recommended)
Use the server-side load generator to avoid browser SSE limits and simulate many users.

Location:
- `kdcube_ai_app/infra/load/test/burst_sse_load.py`

Prerequisites:
- `AUTH_PROVIDER=simple`
- `IDP_DB_PATH=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/api/idp_users.json`
- Ensure admin + registered tokens exist in that file.

### Case 1: 15 registered, 1 message each (no SSE streams)
```
python -m kdcube_ai_app.infra.load.test.burst_sse_load \
  --base-url http://localhost:8010 \
  --registered 15 --admin 0 \
  --messages-per-user 1 \
  --concurrency 10 \
  --monitor
```

### Case 2: 15 registered + 5 admin, 2 messages each (SSE streams open)
```
python -m kdcube_ai_app.infra.load.test.burst_sse_load \
  --base-url http://localhost:8010 \
  --registered 15 --admin 5 \
  --messages-per-user 2 \
  --concurrency 10 \
  --open-sse \
  --monitor
```

### Case 3: Stress queue (burst)
```
python -m kdcube_ai_app.infra.load.test.burst_sse_load \
  --base-url http://localhost:8010 \
  --registered 15 --admin 0 \
  --messages-per-user 4 \
  --concurrency 30 \
  --monitor
```

### How to profile during the test
1. Open Control Plane Monitoring Dashboard:
   - Watch `Queue Analytics`, `Queue Utilization`, `Throttling (Recent)`.
2. Watch log lines:
   - `enqueue_chat_task_atomic result`
   - `acquired task`
   - `Starting task`
3. Redis Browser:
   - Queue size: `<tenant>:<project>:kdcube:chat:prompt:queue:*`
   - Heartbeats: `<tenant>:<project>:kdcube:heartbeat:process:*`

Notes:
- `--open-sse` opens an SSE stream per user (closer to real usage).
- Without `--open-sse`, messages still enqueue and run (events are just not delivered).
