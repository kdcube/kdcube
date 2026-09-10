---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/implement-app-lifecycle-README.md
title: "Implement And Test An App Lifecycle"
summary: "A step-by-step recipe for preparing an app, reconciling configuration, testing readiness, and decommissioning one bundle with explicit durable-data policy."
tags: ["recipe", "app", "bundle", "lifecycle", "deprovision", "testing"]
keywords: ["KDCube app lifecycle recipe", "on_bundle_load", "on_app_deploy", "on_props_changed", "on_app_deprovision", "managed bundle delete", "purge-data", "force-retire", "test app deprovision", "app-owned durable data"]
updated_at: 2026-09-10
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
---
# Implement And Test An App Lifecycle

Use this recipe when an app creates resources that must exist before traffic is
admitted and must be cleaned up when that app is removed.

**Outcome:** one app that can prepare shared resources, react to changed
properties, stop its operational work, retain or explicitly purge its durable
data, and retire without reloading sibling apps.

The complete path is:

```text
descriptor + source
        |
        v
discover -> on_bundle_load -> on_app_deploy -> ready -> admit work
                process          shared                     |
                                                          props change
                                                              |
                                                              v
                                                      on_props_changed

installed app -> kdcube bundle delete <id>
                        |
                        v
              close target admission and intake
                        |
                        v
              on_app_deprovision(purge_data=...)
                        |
                        v
              remove authority and retire target
```

The lifecycle model and hook timing are specified in
[Bundle Lifecycle](../../sdk/bundle/bundle-lifecycle-README.md). This recipe
turns that contract into app code and an acceptance run.

## 1. Inventory What The App Owns

Create `docs/storage/README.md` in the app package before writing cleanup code.
List every resource the app creates, its scope, and its deletion policy:

| Resource | Scope | Operational cleanup | Durable purge |
|---|---|---|---|
| external subscription | tenant + project + app | always unregister | not applicable |
| Redis coordination keys | tenant + project + app prefix | always remove | remove retained keys only with permission |
| PostgreSQL rows | tenant + project columns | remove transient rows | remove business rows only with permission |
| file/object records | app-owned tenant/project prefix | remove temporary files | remove retained files only with permission |

The platform does not infer ownership from table names, key prefixes, or object
paths. The app owns this inventory and the hook that acts on it.

Use a shared PostgreSQL schema safely: prefix table names for the app, include
tenant and project columns, and delete only rows in the current scope. Do not
drop a shared table while another tenant/project may still use it.

## 2. Keep The Entrypoint Thin

Use this package shape:

```text
your-app@1-0/
  entrypoint.py
  services/
    __init__.py
    lifecycle.py
  docs/
    storage/
      README.md
  tests/
    test_lifecycle.py
```

Bundle-local imports are package-relative. SDK imports are absolute.

Put resource work in `services/lifecycle.py`:

```python
from __future__ import annotations

from typing import Any, Mapping


async def deploy_shared_resources(
    *,
    pg_pool: Any,
    tenant: str,
    project: str,
    props: Mapping[str, Any],
) -> None:
    if pg_pool is None:
        raise RuntimeError("PostgreSQL is required")

    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lifecycle_demo_records (
                tenant TEXT NOT NULL,
                project TEXT NOT NULL,
                record_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                PRIMARY KEY (tenant, project, record_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lifecycle_demo_subscriptions (
                tenant TEXT NOT NULL,
                project TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                PRIMARY KEY (tenant, project)
            )
            """
        )
        await reconcile_subscription(
            conn=conn,
            tenant=tenant,
            project=project,
            props=props,
        )


async def reconcile_subscription(
    *,
    conn: Any,
    tenant: str,
    project: str,
    props: Mapping[str, Any],
) -> None:
    subscription = props.get("subscription") or {}
    endpoint = str(subscription.get("endpoint") or "").strip()
    if bool(subscription.get("enabled")) and endpoint:
        await conn.execute(
            """
            INSERT INTO lifecycle_demo_subscriptions (tenant, project, endpoint)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant, project)
            DO UPDATE SET endpoint = EXCLUDED.endpoint
            """,
            tenant,
            project,
            endpoint,
        )
        return

    await conn.execute(
        """
        DELETE FROM lifecycle_demo_subscriptions
        WHERE tenant = $1 AND project = $2
        """,
        tenant,
        project,
    )


async def deprovision_resources(
    *,
    pg_pool: Any,
    tenant: str,
    project: str,
    operation_id: str,
    purge_data: bool,
    logger: Any,
) -> None:
    if pg_pool is None:
        raise RuntimeError("PostgreSQL is required")

    logger.log(
        f"[app.lifecycle] deprovision operation={operation_id} "
        f"purge_data={purge_data}",
        level="INFO",
    )
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            # Operational state is always removed.
            await conn.execute(
                """
                DELETE FROM lifecycle_demo_subscriptions
                WHERE tenant = $1 AND project = $2
                """,
                tenant,
                project,
            )
            # Retained user/business data requires explicit operator permission.
            if purge_data:
                await conn.execute(
                    """
                    DELETE FROM lifecycle_demo_records
                    WHERE tenant = $1 AND project = $2
                    """,
                    tenant,
                    project,
                )
```

These operations are naturally repeatable. When cleanup calls an external API,
send `operation_id` as its idempotency key or record it in an app-owned cleanup
ledger before making the side effect. A retry uses the same logical operation
ID.

## 3. Bind The Existing Lifecycle Hooks

Delegate from `entrypoint.py`:

```python
from __future__ import annotations

from typing import Any, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, bundle_id

from .services.lifecycle import (
    deploy_shared_resources,
    deprovision_resources,
    reconcile_subscription,
)


APP_ID = "your-app@1-0"


@bundle_entrypoint(name="your-app", version="1.0.0", priority=10)
@bundle_id(id=APP_ID)
class YourAppEntrypoint(BaseEntrypoint):
    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "subscription": {
                "enabled": False,
                "endpoint": "",
            }
        }

    async def on_app_deploy(self, **kwargs: Any) -> None:
        await super().on_app_deploy(**kwargs)
        await deploy_shared_resources(
            pg_pool=kwargs.get("pg_pool"),
            tenant=kwargs["tenant"],
            project=kwargs["project"],
            props=kwargs.get("props") or {},
        )

    async def on_props_changed(
        self,
        *,
        previous_props: Dict[str, Any],
        current_props: Dict[str, Any],
        reason: str = "refresh_bundle_props",
        tenant: Optional[str] = None,
        project: Optional[str] = None,
        updated_by: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        await super().on_props_changed(
            previous_props=previous_props,
            current_props=current_props,
            reason=reason,
            tenant=tenant,
            project=project,
            updated_by=updated_by,
            source=source,
        )
        if not tenant or not project or self.pg_pool is None:
            return
        async with self.pg_pool.acquire() as conn:
            await reconcile_subscription(
                conn=conn,
                tenant=tenant,
                project=project,
                props=current_props,
            )

    async def on_app_deprovision(self, **kwargs: Any) -> None:
        await deprovision_resources(
            pg_pool=kwargs.get("pg_pool"),
            tenant=kwargs["tenant"],
            project=kwargs["project"],
            operation_id=kwargs["operation_id"],
            purge_data=bool(kwargs.get("purge_data")),
            logger=kwargs["logger"],
        )
```

Add only the hooks the app needs:

- `on_bundle_load(...)` prepares process-local clients or indexes. If you
  override the base entrypoint implementation, call
  `await super().on_bundle_load(**kwargs)` first.
- `on_app_deploy(...)` creates shared schemas, projections, catalogs, or other
  generation-scoped resources before readiness is published.
- `on_props_changed(...)` reconciles long-lived effects when effective
  properties change. Call the base implementation so inherited UI lifecycle
  behavior remains intact.
- `on_app_deprovision(...)` is async. It removes operational resources on every
  managed delete and removes durable app data only when `purge_data=True`.

Do not put request-user authority into lifecycle hooks. They run with
tenant/project/app-scoped system authority and receive no connected-account
grant from a chat turn.

## 4. Declare Readiness And Properties

In the app row of `bundles.yaml`, keep product configuration in the descriptor:

```yaml
bundles:
  items:
    - id: your-app@1-0
      path: /absolute/path/to/your-app@1-0
      module: entrypoint
      service:
        readiness: independent
      config:
        subscription:
          enabled: true
          endpoint: https://events.example.test/your-app
```

`independent` keeps sibling traffic healthy while this app prepares. This
app's REST, MCP, UI, chat, job, and event doors still return a retryable not-ready
result until its desired generation is ready. Use `required` only when this app
must also determine aggregate processor health.

Apply descriptor changes through the normal configuration path:

```bash
kdcube bundle config apply \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/runtime

kdcube bundle reload your-app@1-0 \
  --workdir /path/to/runtime
```

## 5. Test The App Contract

Use one explicit interpreter and prove its source origin before running tests.
Then run the shared bundle suite and the app's tests:

```bash
PY=/path/to/prepared/python
APP=/absolute/path/to/your-app@1-0

PYTHONPATH=/path/to/kdcube/app/ai-app/src/kdcube-ai-app \
  "$PY" -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path "$APP"

PYTHONPATH=/path/to/kdcube/app/ai-app/src/kdcube-ai-app \
  "$PY" -m pytest -q "$APP/tests"
```

The app tests should prove:

1. deployment creates or reconciles shared resources repeatedly without
   duplication;
2. a property change updates the operational subscription;
3. deprovision with `purge_data=False` removes the subscription and retains
   durable records;
4. deprovision with `purge_data=True` removes only this tenant/project's
   durable records;
5. repeating one `operation_id` does not repeat an external side effect;
6. a cleanup failure is visible and can be retried.

The platform regression tests for operation replay, no-hook behavior, retry,
and async-hook validation are in
`kdcube_ai_app/apps/chat/proc/app_deployment/tests/test_deprovision.py`.

## 6. Run The Live Lifecycle

Use a disposable app ID and a running local runtime. Check the target and at
least one sibling before changing anything:

```bash
WORKDIR=/path/to/runtime
APP=your-app@1-0
SIBLING=another-app@1-0

kdcube bundle status "$APP" --live --json --workdir "$WORKDIR"
kdcube bundle status "$SIBLING" --live --json --workdir "$WORKDIR"
```

Reload the target, wait for readiness, and invoke one real surface it exposes.
Then change a subscription property through descriptor configuration, reload
the target, and verify the operational row changed.

Run ordinary managed deletion first:

```bash
kdcube bundle delete "$APP" --workdir "$WORKDIR"
```

Verify:

- the target descriptor and matching secret row are absent;
- the target is absent from live status;
- the sibling remains ready without a reload;
- the operational subscription is gone;
- durable records remain.

Reinstall the disposable app, seed fresh test data, and run the purge case:

```bash
kdcube bundle delete "$APP" --purge-data --workdir "$WORKDIR"
```

Now the operational and durable app rows for this tenant/project should be
gone. The platform does not claim that result for resources the hook did not
delete.

## 7. Exercise Failure And Recovery

Make the disposable hook fail before its first external or database cleanup,
reload that code, and run:

```bash
kdcube bundle delete "$APP" --workdir "$WORKDIR"
```

Expected result: cleanup fails, the descriptor remains installed, and the
operator can repair the hook and retry the same command. The CLI preserves the
logical operation ID across the interrupted attempt.

Use forced retirement only when leaving cleanup incomplete is the explicit
operator decision:

```bash
kdcube bundle delete "$APP" --force-retire --workdir "$WORKDIR"
```

The target retires, but the result must still say cleanup is incomplete.
`--force-retire` neither grants data-purge permission nor proves cleanup.

The complete command matrix is:

| Command | Hook receives | Failure behavior |
|---|---|---|
| `bundle delete <id>` | `purge_data=False` | keep descriptor for repair and retry |
| `bundle delete <id> --purge-data` | `purge_data=True` | keep descriptor; do not report purge complete |
| `bundle delete <id> --force-retire` | `purge_data=False` | retire and report cleanup incomplete |
| `bundle delete <id> --purge-data --force-retire` | `purge_data=True` | retire; report cleanup and purge incomplete |

`--purge-data` requires a running `chat-proc`, including when combined with
`--force-retire`. Without the processor, trusted app cleanup code cannot run.

## 8. Preserve The Managed Order

Do not remove the bundle row from descriptor input before managed deletion.
Descriptor apply reconciles desired inventory and can retire runtime state, but
it does not invoke `on_app_deprovision(...)` and has no purge authority.

The safe operator order is:

```text
status preflight
    -> managed bundle delete while source and descriptor exist
    -> verify target, sibling, and data outcome
    -> remove the app from reusable seed descriptors
```

The lifecycle is accepted only when the command result, live status, sibling
status, and app-owned storage all agree with the selected policy.
