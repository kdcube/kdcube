---
id: ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md
title: "Bundle Entrypoint Classes"
summary: "Reference for the SDK bundle entrypoint class family: BaseEntrypoint, economics, memory, memory mixin composition, and when to choose each one."
tags: ["sdk", "bundle", "entrypoint", "base-entrypoint", "economics", "memory", "mixin", "widgets"]
keywords: ["BaseEntrypoint", "BaseEntrypointWithEconomics", "MemoryEntrypointMixin", "BaseEntrypointWithMemory", "BaseEntrypointWithEconomicsAndMemory", "bundle entrypoint class", "source folder widget build"]
updated_at: 2026-05-22
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-reserved-platform-properties-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
---
# Bundle Entrypoint Classes

This page explains the SDK entrypoint class family used by Python bundles.

A bundle can technically expose a decorated plain class, but most production
bundles should use one of the SDK entrypoint bases. These classes provide the
runtime glue that is otherwise easy to miss: bundle props/secrets handling,
request context rebinding, communicator setup, bundle storage helpers, static
UI/widget build hooks, model-role defaults, and optional economics or memory
capabilities.

## Entrypoint Versus Per-Message Workflow

The decorated bundle object is the **entrypoint**. It is loaded by the platform
loader, may be cached as a singleton, and owns bundle-level surfaces such as
`@on_message`, `@api`, `@mcp`, `@ui_widget`, `@cron`, `@on_job`,
`on_bundle_load`, and bundle props/secrets handling.

`BaseWorkflow` is a **per-message orchestrator**, not the decorated singleton
bundle entrypoint. The normal pattern is:

```text
loader
  |
  | @bundle_entrypoint(...)
  v
BaseEntrypoint-family instance            may be singleton
  |
  | one request / message / job
  v
BaseWorkflow subclass instance            create inside the turn
  |
  v
React/tools/agent execution
```

Rules:

- decorate a `BaseEntrypoint`-family class as the bundle entrypoint
- create `BaseWorkflow` subclasses inside the entrypoint's per-turn execution
  path
- do not expose a `BaseWorkflow` subclass as a singleton bundle entrypoint
- if a descriptor sets `singleton: true`, the decorated class must not inherit
  `BaseWorkflow`

The runtime enforces this rule for singleton bundles. A singleton decorated
`BaseWorkflow` subclass is rejected because `BaseWorkflow` keeps mutable
per-turn state such as `comm_context`, `comm`, and `runtime_ctx`. That state is
correct when the object is created per message; it is not a singleton contract.

For request-scoped identity inside bundle APIs, widgets, MCP handlers, tools, or
nested runtimes, use:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_request_context,
    get_current_user_identity,
)

ctx = get_current_request_context()
identity = get_current_user_identity()
```

`identity` includes tenant/project, bundle/conversation/turn ids, user id,
username, email, roles, permissions, timezone, and fingerprint when those fields
are present in the authenticated session.

## Class Map

| Class | Import | Use When | Adds |
| --- | --- | --- | --- |
| `BaseEntrypoint` | `kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint` | Default base for most bundles, including bundles with APIs, widgets, main UI, React agents, cron, or jobs. | Bundle props lifecycle, request context, communicator, storage helpers, UI/widget build hooks, base widgets/control surfaces, model/config integration. |
| `BaseEntrypointWithEconomics` | `kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic` | The bundle's chat/agent turn should enforce economics, reservations, quota/funding checks, or emit economics-aware denial/warning behavior. | Everything from `BaseEntrypoint` plus economics runtime managers, reservation defaults, economics hooks, and economics-aware `run(...)` behavior. |
| `MemoryEntrypointMixin` | `kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory` | You need to add the memory capability to a custom entrypoint composition. | Memory defaults, APIs/widgets, reconciliation, snapshots, job handling hooks, and memory extension points. It is a mixin, not a concrete base. |
| `BaseEntrypointWithMemory` | `kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory` | The bundle needs memory but not economics. | `MemoryEntrypointMixin + BaseEntrypoint`. |
| `BaseEntrypointWithEconomicsAndMemory` | `kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory` | The bundle needs both economics and memory. This is the common reference-bundle shape. | `MemoryEntrypointMixin + BaseEntrypointWithEconomics`. |

## Selection Guide

Use `BaseEntrypoint` when:

- the bundle exposes source-folder widgets or main UI
- the bundle needs normal bundle prop/secrets handling
- the bundle uses `BaseWorkflow.build_react(...)`
- the bundle exposes APIs, public routes, cron, or background jobs and wants the
  standard lifecycle hooks

Use `BaseEntrypointWithEconomics` when the bundle is a user-facing chat/agent
surface that should be governed by economics. Do not choose it only because the
bundle has an admin dashboard or reports cost-like data; choose it when runtime
turn execution must participate in economics policy.

Use memory entrypoints when the bundle should expose KDCube memory behavior:
memory widgets, memory tools, memory announcements, reconciliation, snapshots,
or memory maintenance jobs.

For both economics and memory, prefer:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)


class MyEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    ...
```

For memory without economics:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithMemory,
)


class MyEntrypoint(BaseEntrypointWithMemory):
    ...
```

For a custom composition, put the memory mixin before the concrete base:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    MemoryEntrypointMixin,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)


class MyEntrypoint(MemoryEntrypointMixin, BaseEntrypointWithEconomics):
    ...
```

The mixin order matters because the memory mixin contributes configuration,
widgets/APIs, and job handling through cooperative `super()`.

## UI And Widget Build Contract

Source-folder UI and widgets need the SDK UI build contract. All concrete
classes in the `BaseEntrypoint` family provide it.

That means this is correct:

```python
class MyEntrypoint(BaseEntrypointWithMemory):
    ...
```

and this is also correct:

```python
class MyEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    ...
```

Do not read "inherit `BaseEntrypoint`" as "only the bare base class is valid."
The requirement is: inherit a concrete class in the `BaseEntrypoint` family, or
provide an equivalent `_ensure_ui_build(...)` implementation intentionally.

A plain decorated class can declare `@ui_widget(...)` surfaces, but it will not
get the default source-folder build and refresh behavior unless it implements
that contract.

## Configuration Defaults And Mixins

When overriding configuration defaults on a subclass, preserve parent defaults:

```python
class MyEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    @property
    def configuration(self):
        config = dict(super().configuration)
        my_config = dict(config.get("my_feature") or {})
        my_config.setdefault("enabled", True)
        config["my_feature"] = my_config
        return config
```

Do not replace `super().configuration` entirely. Economics, memory, inherited
widgets, role models, execution settings, and other reserved defaults can be
lost that way.

## Background Jobs And Mixins

If a final bundle entrypoint combines SDK mixins and its own `@on_job` handler,
keep one decorated job entrypoint on the final class and dispatch cooperatively:

```python
@on_job(kind="*")
async def handle_job(self, **kwargs):
    handled = await super().handle_job(**kwargs)
    if isinstance(handled, dict) and handled.get("handled"):
        return handled

    job = kwargs.get("job") or {}
    if job.get("work_kind") == "my_bundle.work":
        return await self._handle_my_bundle_work(job)

    return {"handled": False}
```

This lets memory or other SDK mixins consume their own jobs before
bundle-specific `work_kind` dispatch runs.

## Reference Bundles

- `versatile@2026-03-31-13-36` uses
  `BaseEntrypointWithEconomicsAndMemory`.
- `kdcube.copilot@2026-04-03-19-05` uses
  `BaseEntrypointWithEconomicsAndMemory`.
- simpler examples such as `react@2026-02-10-02-44`,
  `react.mcp@2026-03-09`, and `echo.ui@2026-03-30` use `BaseEntrypoint`.
- `eco@2026-02-18-15-06` uses `BaseEntrypointWithEconomics`.

Start from the nearest reference shape rather than stripping mixins out of a
bundle only to make a narrow unit test easier.
