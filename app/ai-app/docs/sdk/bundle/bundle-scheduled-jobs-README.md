---
id: ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md
title: "Bundle Scheduled Jobs"
summary: "Bundle-native cron jobs via the @cron decorator: span semantics, cron resolution, runtime access, and local debug."
tags: ["sdk", "bundle", "cron", "scheduled-jobs", "scheduler", "proc"]
keywords: ["@cron", "cron decorator", "CronJobSpec", "BundleSchedulerManager", "span", "process", "instance", "system", "expr_config", "cron_expression", "timezone", "tz_config", "enabled_config", "bundle disabled", "scheduled jobs", "BUNDLES_YAML_DESCRIPTOR_PATH"]
see_also:
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
---
# Bundle Scheduled Jobs

Bundle methods decorated with `@cron` are automatically discovered by proc and
scheduled as recurring background jobs. No manual wiring or ad hoc loops needed.

---

## Quick start

```python
from kdcube_ai_app.infra.plugin.agentic_loader import cron

class MyBundle(BaseEntrypoint):

    @cron(
        alias="rebuild-indexes",
        cron_expression="0 * * * *",
        span="system",
    )
    async def rebuild_indexes(self) -> None:
        ...
```

Proc picks this up on the next registry reconcile and runs `rebuild_indexes`
once per hour across the whole system.

---

## Decorator reference

```python
@cron(
    alias: str | None = None,
    cron_expression: str | None = None,
    expr_config: str | None = None,
    timezone: str | None = None,
    tz_config: str | None = None,
    span: str = "system",
    enabled_config: str | None = None,
)
```

| Argument | Type | Description |
|---|---|---|
| `alias` | `str \| None` | Stable job identifier. Used in Redis lock keys and logs. Defaults to method name. |
| `cron_expression` | `str \| None` | Inline cron expression, e.g. `"*/15 * * * *"`. |
| `expr_config` | `str \| None` | Dot-path into bundle props/config, e.g. `"routines.reindex.cron"`. Wins over `cron_expression`. |
| `timezone` | `str \| None` | IANA timezone for cron interpretation, e.g. `"Europe/Berlin"`. Defaults to UTC. |
| `tz_config` | `str \| None` | Dot-path into bundle props/config for the timezone override. Wins over `timezone`. |
| `span` | `str` | Exclusivity: `"process"`, `"instance"`, `"system"`. Default: `"system"`. |
| `enabled_config` | `str \| None` | Dot-path into bundle props that resolves to a boolean. Falsy → job not scheduled. `None` means always enabled. |

---

## Bundle-level enabled override

If the bundle's `@agentic_workflow(enabled_config=...)` resolves to a falsy
value from bundle props, **all** scheduled jobs for that bundle are skipped
during reconcile — regardless of individual job `enabled_config` or
`expr_config` values. The per-job checks are never reached.

This is enforced in `bundle_scheduler.py` (`reconcile`) before the per-job
loop. Use it as a single kill-switch to disable the entire bundle, including
all its cron jobs, without touching each job individually.

---

## Cron source rules

1. If `expr_config` is set — resolve the dot-path against bundle props/config.
   - Missing / blank / exactly `"disable"` (case-insensitive) → job **not** scheduled.
   - Do **not** fall back to `cron_expression`.
2. Else if `cron_expression` is set — use it.
3. Else — inert; nothing is scheduled.

When both are set, `expr_config` wins at runtime. `cron_expression` still
appears in the bundle descriptor as the declared default/fallback.

---

## Span semantics

`span` defines the exclusivity scope of execution, not the cron source.

### `process`

- Runs independently in every proc worker process.
- No Redis lock.
- If 4 proc processes are running, the job may execute 4 times per tick.
- Overlap within one process is prevented by an in-process flag.

### `instance`

- Exactly one execution per host (`INSTANCE_ID`).
- Multiple proc processes on the same instance compete; only the one that
  acquires the lock runs.

Redis lock key:
```
bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}:{instance_id}
```

### `system`

- Exactly one execution across the whole deployed system for that
  tenant/project/bundle/job.
- All instances and all processes compete; only one wins.

Redis lock key:
```
bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}
```

### Redis unavailability

For `instance` and `system`:

- If Redis is unavailable, the tick is **skipped**.
- A warning is logged.
- The scheduler task stays alive and will try again on the next tick.
- Jobs are **not** silently degraded to `process` behavior.

### Span default

Omitting `span` or passing an empty string defaults to `"system"`.
An unrecognised value raises `ValueError` at decoration time (not at runtime).

---

## Method shape

Instance methods on the bundle entrypoint class. No required arguments
besides `self`.

Async preferred:

```python
@cron(cron_expression="*/5 * * * *", span="process")
async def check_queue(self) -> None:
    ...
```

Sync also supported — run via `asyncio.to_thread`:

```python
@cron(cron_expression="0 0 * * *", span="system")
def nightly_report(self) -> None:
    ...
```

---

## Runtime access

Scheduled jobs run headlessly — no user session or SSE stream. The bundle
instance is constructed through the standard loader path, so these are all
available inside a cron method:

| Surface | Notes |
|---|---|
| `self.bundle_props` | Full props loaded from Redis, refreshed before the method runs |
| `self.bundle_prop("some.path")` | Typed prop accessor |
| `self.redis` | Dedicated Redis client (separate pool, does not contend with the processor's shared pool) |
| `self.pg_pool` | Postgres pool — same singleton used by the rest of the process |
| Secrets | Same resolution path as normal bundle execution |
| `self.config` | Real `Config` object; `self.config.ai_bundle_spec.id` is set correctly |

What is **not** available:

- `self.comm` / communicator — there is no user session or SSE stream target
- `self.comm_context` — not bound in headless mode

---

## `expr_config` resolution chain

When `expr_config` is set, the effective cron expression is resolved in this
order:

1. **Redis bundle props** — live values written by the bundle config update API.
2. **bundles.yaml** — via `read_plain("b:<path>")` using
   `BUNDLES_YAML_DESCRIPTOR_PATH` (local debug / static config).
3. **assembly.yaml** — via `read_plain("<path>")` using
   `ASSEMBLY_YAML_DESCRIPTOR_PATH` (local debug / static config).

Redis always wins when a value is present. The YAML fallbacks are primarily
for local development when Redis holds no props.

---

## Live updates (props change handling)

When bundle props change (via `bundles.update` or any config update), proc
reconciles all scheduled jobs for affected bundles automatically — no restart
required:

- Cron expression changes → old task cancelled, new one started.
- Value becomes `"disable"` → task cancelled.
- Previously disabled job gets a valid value → task started.

The same reconcile path runs on startup and after every bundle registry update.

---

## Overlap guard

If a job is still running when the next tick arrives, the new tick is skipped.

- `span="process"` — in-process `_running` flag per job.
- `span="instance"` / `"system"` — the held Redis lock prevents overlap
  naturally. Lock TTL is 1 hour; renewed every 60 seconds while the job runs.

The skip is logged at `INFO` level.

---

## Error handling

Exceptions raised by a scheduled job are caught, logged with full traceback,
and the scheduler loop continues with future ticks.
A single failing job does not affect other jobs or proc.

---

## Multiple cron methods on one bundle

Each method gets its own asyncio task and its own Redis lock key (by `alias`).

```python
@cron(alias="heartbeat", cron_expression="* * * * *", span="system")
async def heartbeat(self) -> None:
    ...

@cron(alias="nightly-cleanup", cron_expression="0 2 * * *", span="system")
async def nightly_cleanup(self) -> None:
    ...
```

---

## Bundle descriptor

Every bundle descriptor returned by the integrations endpoint includes the
declared scheduled jobs. Both `cron_expression` and `expr_config` are shown as
declared on the decorator — not the runtime-resolved effective value.
If `expr_config` is set and resolves to a different expression at runtime, the
descriptor still shows the original `cron_expression` default.

```json
{
  "scheduled_jobs": [
    {
      "method_name": "rebuild_indexes",
      "alias": "rebuild-indexes",
      "cron_expression": "0 * * * *",
      "expr_config": "routines.reindex.cron",
      "span": "system"
    }
  ]
}
```

---

## Implementation files

| File | Purpose |
|---|---|
| `infra/plugin/agentic_loader.py` | `@cron` decorator, `CronJobSpec`, `BundleInterfaceManifest.scheduled_jobs`, manifest discovery |
| `apps/chat/sdk/runtime/bundle_scheduler.py` | `BundleSchedulerManager`, `resolve_effective_cron`, per-job loops, Redis locking |
| `apps/chat/processor.py` | Creates the manager; calls `reconcile` on startup and after every registry/props change |
| `apps/chat/proc/rest/integrations/integrations.py` | Exposes `scheduled_jobs` in the bundle descriptor |
| `apps/chat/sdk/runtime/tests/bundle_scheduler/` | Unit tests: loader discovery, cron resolution, scheduler lifecycle, descriptor |

---

## Reference example

`apps/chat/sdk/examples/bundles/echo.ui@2026-03-30/entrypoint.py`:

```python
@cron(
    alias="echo-heartbeat",
    cron_expression="* * * * *",
    expr_config="routines.heartbeat.cron",
    span="system",
)
async def scheduled_heartbeat(self) -> None:
    """
    Fires every minute by default.
    Override or disable via bundle props:
      routines.heartbeat.cron: "*/5 * * * *"   # change interval
      routines.heartbeat.cron: "disable"        # disable the job
    """
    ...
```

Because `expr_config` is set, the inline `cron_expression` is only used when
no Redis props or YAML config override is present.
