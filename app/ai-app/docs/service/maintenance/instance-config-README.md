---
id: ks:docs/service/maintenance/instance-config-README.md
title: "Instance Config"
summary: "Instance configuration for workers, capacity, and DB pools."
tags: ["service", "maintenance", "instance", "capacity"]
keywords: ["workers", "processes_per_instance", "pg_max_connections", "capacity settings"]
see_also:
  - ks:docs/service/maintenance/connection-pooling-README.md
  - ks:docs/service/maintenance/requests-monitoring-README.md
  - ks:docs/service/service-config-README.md
---
# Instance Config Maintenance

Quick reference for configuring a single chat service instance (workers, capacity, DB pools) and keeping it stable in dev/EC2.

## Source of Truth

Runtime capacity is driven by `GATEWAY_CONFIG_JSON.service_capacity.<component>`:
- `processes_per_instance` = number of Uvicorn workers for that service.
- `concurrent_requests_per_process` = max concurrent chats per worker (processor) or admission capacity (ingress).
- `avg_processing_time_seconds` = expected average turn time for capacity calculations.

Each service sets `GATEWAY_COMPONENT` at startup (`ingress` or `proc`) to select its slice.

Admin updates are stored in Redis (tenant/project scoped) and override `GATEWAY_CONFIG_JSON` on restart.

## Startup Precedence

On startup the gateway config is loaded in this order:
1. Redis cache for the selected tenant/project (if present).
2. Env defaults / `GATEWAY_CONFIG_JSON`.

If you want env to apply, clear the cached config first (Control Plane → Gateway Configuration → “Clear Cached Config”), then restart.

Endpoint policy lists are part of the gateway config:
- `guarded_rest_patterns` (REST endpoints that should be gated)
- `bypass_throttling_patterns` (public endpoints that skip rate limiting)

## Worker Count

Worker count comes from `GATEWAY_CONFIG_JSON.service_capacity.<component>.processes_per_instance`
when running the service `web_app.py` directly.

Changing `processes_per_instance` requires a service restart to take effect.

## Postgres Pool Sizing (per worker)

Each worker creates its own asyncpg pool.

Pool size rules:
- `GATEWAY_CONFIG_JSON.pools.<component>.pg_pool_max_size` if set.
- Else `service_capacity.<component>.concurrent_requests_per_process`.
- `pg_pool_min_size` defaults to `0` if not set.

Estimated DB connections:
- Per instance: `processes_per_instance × pg_pool_max_size`
- Total system: `per_instance × instance_count`

Keep `total_connections` comfortably below Postgres `max_connections`.

## RDS max_connections

`max_connections` is global per Postgres server (all clients combined).

Safe guideline:
- Chat should target 50–60% of max to leave headroom for other services and tools.

Example:
- `max_connections=80`
- `processes_per_instance=4`, `pg_pool_max_size=8`
- Chat uses ~32 connections → OK.

## Quick Checks

Confirm config source at startup:
- Look for logs:
  - `Gateway config source: redis-cache ...` or
  - `Gateway config source: env (GATEWAY_CONFIG_JSON) ...`

Confirm worker count:
- Monitoring UI → Capacity Transparency → Configured/Actual/Healthy processes.

Confirm DB pool sizing:
- Set `pools.<component>.pg_pool_max_size` and confirm in logs:
  - `PG pool sizing: {'min_size': 0, 'max_size': N} ...`

Confirm DB max connections:
- `psql ... -c "SHOW max_connections;"`

## Example Config (Dev EC2)

```json
{
  "service_capacity": {
    "ingress": {
      "concurrent_requests_per_process": 5,
      "processes_per_instance": 2,
      "avg_processing_time_seconds": 25
    },
    "proc": {
      "concurrent_requests_per_process": 8,
      "processes_per_instance": 4,
      "avg_processing_time_seconds": 25
    }
  }
}
```

Result:
- Ingress workers = 2; processor workers = 4
- Pool per worker = 8 (unless `pools.<component>.pg_pool_max_size` set)
- Total chat DB connections ≈ 32 (processor side)

## If You See “Too Many Connections”

Actions:
1. Lower `pools.<component>.pg_pool_max_size` or `concurrent_requests_per_process`.
2. Lower `processes_per_instance`.
3. Increase RDS `max_connections` (parameter group + reboot).
4. Add a pooler (pgbouncer / RDS Proxy).

## Handy Commands

Start chat service (loads `.env` from `app/ai-app`):
```bash
cd /Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app
PYTHONPATH=/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/services/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.api.web_app
```

Check `max_connections`:
```bash
PGPASSWORD='<password>' psql "host=<rds-endpoint> port=5432 dbname=<db> user=<user> sslmode=disable" \
  -c "SHOW max_connections;"
```

## Ops Notes

- Reset to env in the Control Plane writes env defaults to Redis and overrides cache for all instances.
- Clear Cached Config deletes the Redis key so the next restart uses env/`GATEWAY_CONFIG_JSON`.
- Changing worker count always requires restart.


```shell
 set -euo pipefail

  echo "== OS / Kernel ==" && uname -a && cat /etc/os-release || true
  echo

  echo "== CPU ==" && lscpu || true
  echo

  echo "== Memory ==" && free -h || true
  echo

  echo "== Disk ==" && df -h || true
  echo

  echo "== Load ==" && uptime || true
  echo

  echo "== EC2 Instance Type (IMDSv2) =="
  TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60") || true
  if [ -n "${TOKEN:-}" ]; then
    curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-type && echo
  else
    echo "IMDSv2 token fetch failed (IMDS may be disabled)."
  fi
```
