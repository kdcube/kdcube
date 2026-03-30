---
id: ks:docs/service/maintenance/connection-pooling-README.md
title: "Connection Pooling"
summary: "Connection pooling guidance for Redis and Postgres in chat services."
tags: ["service", "maintenance", "pools", "redis", "postgres"]
keywords: ["pool size", "max connections", "health", "pool limits"]
see_also:
  - ks:docs/service/maintenance/requests-monitoring-README.md
  - ks:docs/service/maintenance/instance-config-README.md
  - ks:docs/arch/architecture-long.md
---
**Connection Pooling (Chat Services)**

This doc describes how **Redis** and **Postgres** pools are created, shared, and closed **per worker (process)** in the chat services.
It also calls out an important difference in the current architecture:
- `ingress` still starts the legacy three-client Redis bundle
- `proc` now runs on a **single steady-state shared async Redis pool per worker**

---

**Where Pools Are Created (Ingress + Processor)**

Pools are created once per process and stored in `app.state` during FastAPI lifespan.
Each service sets `GATEWAY_COMPONENT` so it selects the **component slice** of the gateway config:
- `ingress` (SSE/REST ingress)
- `proc` (processor + integrations)

- Postgres:
  - `get_pg_pool()` → `app.state.pg_pool`
  - Closed on shutdown via `pg_pool.close()`
- Redis:
  - Ingress / metrics:
    - `get_redis_clients()` →
      - `app.state.redis_async`
      - `app.state.redis_async_decode`
      - `app.state.redis_sync`
  - Proc:
    - `get_shared_async_redis_client()` →
      - `app.state.redis_async`
      - `app.state.redis_async_decode = None`
      - `app.state.redis_sync = None`
  - Closed on shutdown via `close_redis_clients()`

---

**Redis Pools (Per Process)**

**Ingress / metrics**

Each ingress or metrics worker currently creates **three shared Redis pools**:

1. `redis_async`
   - async client
   - `decode_responses=False`
2. `redis_async_decode`
   - async client
   - `decode_responses=True`
3. `redis_sync`
   - sync client
   - `decode_responses=False`

These are shared across gateway, SSE, monitoring, bundles, and related service paths in that process.

**Proc**

Each proc worker now creates **one steady-state shared Redis pool**:

1. `redis_async`
   - async client
   - `decode_responses=False`
   - shared by queue pop, processor execution, gateway helpers, gateway-config pub/sub, and health monitoring

Proc intentionally does **not** keep steady-state `redis_async_decode` or `redis_sync` clients in `app.state`.
During startup, proc may touch Redis while loading gateway config from cache; those bootstrap clients are closed before the steady-state pool is created so the running worker uses the final gateway-config slice.

**Important**
- Pub/sub and blocking Redis calls consume connections **from the pool**; they are not an extra cap on top of `max_connections`.
- Separate code-executor containers or Fargate tasks are **not** part of the proc worker pool and may open their own Redis connection(s).

**Size control**
- `GATEWAY_CONFIG_JSON.pools.<component>.redis_max_connections` caps the pool size selected for that component.

Approx steady-state Redis connections per process:

- Ingress / metrics:

```
max_redis_conns_per_process ≈ 3 * redis_max_connections
```

- Proc:

```
max_redis_conns_per_process ≈ redis_max_connections
```

If `redis_max_connections` is **unset**, the pool size is unbounded and can grow with load.

**Client names (for `CLIENT LIST`)**

All pools set a Redis client name automatically so you can group them by service:

```
<REDIS_CLIENT_NAME or SERVICE_NAME or APP_NAME>:<INSTANCE_ID or HOSTNAME>:<PID>:<pool_kind>
```

Pool kinds:
- `async`
- `async_decode`
- `sync`

In proc, the normal steady-state client name you should expect is `...:async`.

To override the prefix, set `REDIS_CLIENT_NAME`.

**Where implemented**
- `kdcube_ai_app/infra/redis/client.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/resolvers.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`

---

**Postgres Pool (Per Process)**

Each worker creates **one asyncpg pool**:

```
app.state.pg_pool
```

**Size control**
- `GATEWAY_CONFIG_JSON.pools.<component>.pg_pool_max_size` → hard cap for pool size
- `GATEWAY_CONFIG_JSON.pools.<component>.pg_pool_min_size` → minimum connections
- If pools are **not set**, it defaults to:

```
gateway_config.service_capacity.<component>.concurrent_requests_per_process
```

**Approx Postgres connections per instance**

```
pg_conns_per_instance ≈ workers * pg_pool_max_size
```

Where:
- `workers` = `service_capacity.<component>.processes_per_instance`
- `pg_pool_max_size` = gateway config pool size or fallback

**Where implemented**
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/resolvers.py` → `get_pg_pool()`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py` → lifespan startup/shutdown

---

**Postgres Env Quicklist**

- `PGHOST` / `POSTGRES_HOST` → database host.
- `PGPORT` / `POSTGRES_PORT` → database port.
- `PGUSER` / `POSTGRES_USER` → database user.
- `PGPASSWORD` / `POSTGRES_PASSWORD` → database password.
- `PGDATABASE` / `POSTGRES_DATABASE` → database name.
- `PGSSL` / `POSTGRES_SSL` → SSL mode.
- Pool sizing is controlled via `GATEWAY_CONFIG_JSON.pools.<component>.*`.

---

**Operational Notes**

- Redis and Postgres pools are **per process**. Total connections scale with worker count.
- When you raise `service_capacity.<component>.processes_per_instance`, you also raise total Postgres and Redis connections.
- Proc connection budgeting should assume **one shared async Redis pool per worker**.
- Ingress and metrics connection budgeting should still assume the legacy **three-pool** Redis layout.
- If `pools.<component>.redis_max_connections` changes at runtime, existing workers keep the pool size they started with until restart.
- For Redis max clients, ensure:

**Quick validation tool**
Run this to print the effective per-process limits from gateway config:

```bash
python -m kdcube_ai_app.infra.tools.gateway_config_dump
```

For proc:

```
total_redis_connections_proc ≈ processes * redis_max_connections
```

For ingress / metrics:

```
total_redis_connections ≈ processes * 3 * redis_max_connections
```

fits within Elasticache `maxclients`.

- For Postgres, ensure:

```
total_pg_connections ≈ processes * pg_pool_max_size
```

**SSE capacity (per process)**

`limits.ingress.max_sse_connections_per_instance` is enforced **per worker process** because each
Uvicorn worker owns its own in‑process `SSEHub`.

Total per instance:

```
total_sse_connections_per_instance ≈ max_sse_connections_per_instance * processes_per_instance
```

fits within `max_connections` on the DB.

---

**Redis Connection Monitor (Centralized)**

Chat starts a lightweight Redis health monitor in-process:

- Tracks `PING` health on the shared async client.
- Emits **up/down** events to registered listeners.
- Useful for components that need to **resubscribe** (e.g., pubsub listeners).

Env:
- `REDIS_HEALTHCHECK_INTERVAL_SEC` (default: `5`)
- `REDIS_HEALTHCHECK_TIMEOUT_SEC` (default: `2`)

Shared Redis clients also set:
- `socket_connect_timeout=5`
- `health_check_interval=30`
- `socket_keepalive=True`
- `retry_on_timeout=True`

**Redis Env Quicklist**

- `REDIS_URL` sets the Redis endpoint used by all shared pools and monitors.
- `pools.<component>.redis_max_connections` caps the per-process Redis pool size.
- `GATEWAY_CONFIG_JSON.pools.<component>.redis_max_connections` sets the pool cap.
- `REDIS_CLIENT_NAME` sets the client name prefix shown in `CLIENT LIST`.
- `REDIS_HEALTHCHECK_INTERVAL_SEC` sets the Redis health poll interval.
- `REDIS_HEALTHCHECK_TIMEOUT_SEC` sets the Redis health poll timeout.
- `CB_RELAY_IDENTITY` / `CB_ORCHESTRATOR_TYPE` control the relay channel namespace.
- `SSE_CLIENT_QUEUE` controls per-client SSE queue size (burst safety).
- `CHAT_SSE_REJECT_ANONYMOUS` rejects anonymous SSE connections when `1`.

Access:
- `app.state.redis_monitor` (created during FastAPI lifespan)

You can register a listener:

```python
monitor = request.app.state.redis_monitor
monitor.add_listener(lambda state, err: print("redis:", state, err))
```

**Automatic Redis Reconnect Resync**

When Redis reconnects, the chat service automatically:
- Rebuilds SSE relay subscriptions from the active SSE hub state.
- Reconnects gateway config pubsub listener.
- For proc, queue/config listener failures trigger a shared-pool socket disconnect so the next Redis call reconnects through the same client.

Look for logs:
- `[RedisMonitor] Redis connection recovered`
- `[SSEHub] resync relay reason=redis_reconnect ...`
- `[gateway.config] Subscribed to ...`
- `Queue pop timed out after ...; disconnecting shared pool`
- `Resetting shared async Redis pool for processor: ...`

Proc heartbeat metadata now also exposes:
- `processor.current_load`
- `processor.active_tasks`
- `processor.queue_loop_lag_sec`
- `processor.config_loop_lag_sec`
- `processor.last_queue_error`
- `processor.last_config_error`

---

**Code Executor Context (Docker/Fargate)**

The code executor runs in a **separate process/container** (Docker or Fargate).  
If a tool inside executed code emits chat events, it will create its **own Redis connection** in that isolated runtime.

Key points:
- Executor Redis is **not shared** with the main chat service pools.
- It is **lazy**: the Redis connection is created only when the tool emits via the communicator.
- This uses the standard communicator path (`ChatRelayCommunicator` → `ServiceCommunicator._ensure_async()`).

Relevant code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py` → `make_chat_comm(...)` builds the communicator.
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/comm_ctx.py` → `get_comm()` returns the communicator lazily.
- `src/kdcube-ai-app/kdcube_ai_app/infra/orchestration/app/communicator.py` → `_ensure_async()` creates the Redis client on first publish.

Operational impact:
- Expect **+1 Redis connection per executor container** that actually emits events.
- If emitters are rare, this is negligible; if you scale executors, budget Redis `maxclients` accordingly.

---

**How To Check Limits (CLI)**

**Postgres**

Check max connections from SQL:

```bash
psql "host=<host> port=5432 dbname=<db> user=<user> sslmode=require" -c "SHOW max_connections;"
```

If you need a password prompt:

```bash
PGPASSWORD="<password>" psql "host=<host> port=5432 dbname=<db> user=<user>" -c "SHOW max_connections;"
```

Other useful counters:

```sql
SHOW max_connections;
SELECT count(*) FROM pg_stat_activity;
SELECT name, setting FROM pg_settings WHERE name IN ('max_connections','shared_buffers','work_mem');
```

**Redis**

Using redis-cli (works for ElastiCache if you can connect to the endpoint):

```bash
redis-cli -h <host> -p 6379 -a "<password>" INFO clients
```

Look for:
- `maxclients`
- `connected_clients`

ElastiCache usually disables `CONFIG` commands, so this may fail:

```bash
redis-cli -h <host> -p 6379 -a "<password>" CONFIG GET maxclients
```

If `CONFIG` is disabled, rely on `INFO clients`.

Additional useful counters:

```bash
redis-cli -h <host> -p 6379 -a "<password>" INFO stats
```

Check:
- `rejected_connections` (non‑zero means you *did* hit maxclients)
- `total_connections_received` (how many connections were opened since start)

**Who is holding connections (snapshot)**

```bash
redis-cli -h <host> -p 6379 -a "<password>" CLIENT LIST
```

To aggregate by client address:

```bash
redis-cli -h <host> -p 6379 -a "<password>" CLIENT LIST | \
  awk -F' ' '{addr=""; name=""; for(i=1;i<=NF;i++){if($i ~ /^addr=/) addr=substr($i,6); if($i ~ /^name=/) name=substr($i,6)}; print addr, name}' | \
  sort | uniq -c | sort -nr | head
```

---

**How To Change Limits**

**RDS Postgres**

`max_connections` is controlled by **DB parameter group** and also bounded by instance memory.

High-level steps:
1. Identify the DB parameter group:

```bash
aws rds describe-db-instances --db-instance-identifier <id> \
  --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text
```

2. Update the parameter group (example):

```bash
aws rds modify-db-parameter-group \
  --db-parameter-group-name <param-group> \
  --parameters "ParameterName=max_connections,ParameterValue=200,ApplyMethod=pending-reboot"
```

3. Reboot the instance for it to take effect:

```bash
aws rds reboot-db-instance --db-instance-identifier <id>
```

Note: If you hit memory limits, you may need a larger instance class.

**ElastiCache Redis**

`maxclients` is **not** typically editable in ElastiCache; it is determined by the node type.

To increase it, **scale up the node type** (via console or AWS CLI):

```bash
aws elasticache modify-replication-group \
  --replication-group-id <id> \
  --cache-node-type <new-node-type> \
  --apply-immediately
```

If you use a standalone cluster:

```bash
aws elasticache modify-cache-cluster \
  --cache-cluster-id <id> \
  --cache-node-type <new-node-type> \
  --apply-immediately
```
