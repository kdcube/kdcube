---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/automations/automations-sdk-solution-README.md
title: "Automations SDK Solution"
summary: "Reusable scheduled/executable automation component for KDCube bundles: automation storage, execution journals, artifact recovery, ReAct tools, and skills."
tags: ["sdk", "solutions", "automations", "scheduler", "executions", "artifacts", "react", "identity-authority"]
updated_at: 2026-06-26
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---

# Automations SDK Solution

`kdcube_ai_app.apps.chat.sdk.solutions.automations` is the reusable automation component for
bundles that need durable actionable work: saved automations, schedules, fresh job
executions, execution journals, output artifacts, and model-facing tools.

The bundle owns product policy and routes. The SDK owns the reusable automation
mechanics.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.solutions.automations
  storage.py              Markdown + YAML front matter automation storage and SQLite FTS automation index
  executions_storage.py   Execution journal JSON files and SQLite FTS execution index
  async_storage.py        Async wrappers for file/SQLite automation stores
  execution_artifacts.py  Execution artifact indexing, download filtering, materialization
  operations.py           Configurable automation CRUD/search/run/download operations
                          for bundle routes and widgets
  due.py                  Configurable due-automation scanner and background-job handler
  tools.py                ReAct automation-management tools under alias `automations`
  job_tools.py            ReAct saved-job tools under alias `automation_job`
  common.py               Shared tool-context imports
  skills/automation/automations  Built-in `automation.automations` skill
  skills/automation/job          Built-in `automation.job` skill
```

## Data Model

Automation definitions are Markdown assets with YAML front matter:

```text
<storage_root>/automations/<user_id>/<automation_id>.md
<storage_root>/indexes/automations/<user_id>/automations.sqlite
```

Execution records are separate from automation definitions:

```text
<storage_root>/automation_executions/<user_id>/<automation_id>/<execution_id>.json
<storage_root>/indexes/automation_executions/<user_id>/executions.sqlite
```

The automation definition describes what should be done. The execution record
describes what happened during one run: status, summary, logs, result JSON,
conversation/turn ids, and user-visible file artifacts.

## Model-Facing Tools

Main conversation tools:

```python
{
    "module": "kdcube_ai_app.apps.chat.sdk.solutions.automations.tools",
    "alias": "automations",
    "use_sk": True,
}
```

Saved-job tools:

```python
{
    "module": "kdcube_ai_app.apps.chat.sdk.solutions.automations.job_tools",
    "alias": "automation_job",
    "use_sk": True,
}
```

`automations.*` lets the main assistant create, list, edit, link, run, and search
automations and prior outputs. `automation_job.*` is for a fresh job conversation that is
executing one saved automation; automation id and execution id come from injected runtime
context, not model-authored parameters.

## Automation Execution Context

Automation execution code should read runtime ids through the SDK helper instead
of decoding processor job envelopes directly:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations.common import (
    extract_automation_execution_context,
    extract_automation_execution_context_from_scope,
)
```

There are two valid runtime shapes:

```text
direct automation execution
  bundle_call_context.kind = automation_execution
  bundle_call_context.automation_id = ...
  bundle_call_context.execution_id = ...

queued background job
  bundle_call_context.kind = background_job
  bundle_call_context.job_id = ...
  bundle_call_context.payload.automation_id = ...
  bundle_call_context.payload.execution_id = ...
```

The literal field is still named `bundle_call_context` because that is the
current platform API. Treat it as app-owned call context.

### Actor, Storage User, And Authority User

Automation executions can be started by a surface-local identity. A Telegram
Mini App user is the clearest example:

```text
actor/storage identity: telegram_100200300
linked platform user:  a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d
```

Those identities are not the same and should not be collapsed. The automation
may keep `telegram_100200300` as the app storage/audit owner while using the
linked platform user as the role/economics authority.

The conversion happens once when detached work is enqueued or picked up. After
that, the execution context already carries the authority. Role checks should
read the context; they should not perform surface-specific link lookup again.

Canonical envelope:

```text
source.identity_authority
bundle_call_context.identity_authority
  actor_user_id       = telegram_100200300
  storage_user_id     = telegram_100200300
  platform_user_id    = a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d
  economics_user_id   = a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d
  platform_roles      = ["kdcube:role:super-admin"]
  platform_permissions = [...]
  economics_budget_bypass = true
  identity_provider   = telegram
  identity_provider_subject = 100200300
```

Runtime binding then projects this into:

```text
REQUEST_CONTEXT.user
  user_id     = telegram_100200300      # actor/storage identity
  roles       = ["kdcube:role:super-admin"]
  permissions = [...]

ReAct state
  user           = telegram_100200300
  economics_user = a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d
```

The app keeps using the actor/storage identity for app data. Economics checks
and platform role checks use the already-bound authority fields from the
execution context.

Do not map surface-local roles such as Telegram admin directly to platform
privileged. A Telegram admin flag is local app authorization. Platform authority
must come from a linked platform principal and the platform authority resolver.

This context is cross-runtime because `bundle_call_context` is part of the
portable context room documented in
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

### Where This Works Today

| Surface | Current behavior |
| --- | --- |
| Scheduled automation scanner | Supported. `due.configure_due_automations(..., scheduler_identity_resolver=...)` lets the app provide a link-derived identity context. The SDK stores `source.identity_authority` in the durable queued execution. |
| Background job pickup / `@on_job` | Supported. `operations.run_automation_execution(...)` normalizes the durable source into `bundle_call_context.identity_authority`, stamps scoped `REQUEST_CONTEXT.user`, and passes `economics_user` into ReAct state. |
| Default ReAct automation job | Supported. The actor remains `state.user`; economics uses `state.economics_user`; roles/permissions are carried in the scoped request context and state. |
| Custom `execute_automation_job(...)` | Supported if the custom executor runs through the SDK operation path. It receives a scoped request context and `bundle_call_context.identity_authority`. |
| Manual automation run from an authenticated browser session | Usually does not need link projection because the browser request already has a platform `UserSession`. If the manual run is queued for later under a surface-local actor, the enqueue source must include the same authority envelope. |
| Telegram Mini App interactive requests | Telegram init data proves the Telegram actor for that request. Platform authority projection should route through the Connection Hub request-auth bridge; scheduled automations use the configured scheduler identity resolver path because they are detached durable work. |
| Generic API/MCP/Data Bus calls | They must either arrive with a platform-authenticated request context or carry proof/authority metadata that Connection Hub, the producing provider, or the app can resolve into the same authority envelope. |

### Request-Auth Bridge And Detached Work

The current automation mechanism solves detached scheduled execution. Interactive
provider requests should use the Connection Hub request-auth bridge; scheduled
jobs should store the resulting authority in the durable source before they run.

The shared shape is:

```text
incoming request / event / job
  |
  | authenticate proof
  |   browser cookie, Telegram initData, webhook signature, API key, MCP token...
  v
request-auth selector or durable scheduler resolver
  |
  | emits actor identity
  | resolves linked platform principal through Connections
  | asks platform authority resolver for roles/permissions/budget-bypass facts
  v
execution context
  |
  | REQUEST_CONTEXT.user already carries effective roles
  | BUNDLE_CALL_CONTEXT.identity_authority preserves actor/economics split
  v
role checks, economics, ReAct, tools, child runtimes
```

That authorizer belongs at the channel ingress boundary. It should be generic
SDK/platform infrastructure so every app does not repeat:

- Telegram identity proof validation;
- external identity -> platform user lookup;
- platform role/permission resolution;
- authority envelope construction;
- context binding before tools/ReAct/runtime boundaries.

For app tools that already have a `bundle_tool_context.scope()` result:

```python
from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import scope
from kdcube_ai_app.apps.chat.sdk.solutions.automations.common import (
    extract_automation_execution_context_from_scope,
)

sc = scope()
automation_context = extract_automation_execution_context_from_scope(sc)
automation_id = automation_context.get("automation_id")
execution_id = automation_context.get("execution_id")
```

For entrypoint/app code that already has the raw call context:

```python
automation_context = extract_automation_execution_context(bundle_call_context)
```

This keeps `task_id` reserved for the platform processor/event identity and
keeps `automation_id` reserved for the automation domain.

Saved automation jobs should treat integration tool failures as execution facts, not
as user intent changes. For email-processing jobs, `email.process_user_emails`
may return `email_processor_failed` when the stateful Claude/MCP processor fails
before recording an authoritative result. The job prompt instructs the agent to
retry the same tool call when rounds remain, or record the execution as failed.
It must not reinterpret that failure as "no new emails" or replace it with a
web/raw-mailbox fallback.

## Skills

The automation skills are loaded as SDK solution skills:

```text
automation.automations  -> create/list/update/delete/link automations and recover execution output
automation.job    -> execute one saved automation and update its execution journal
```

A bundle enables them through the consuming agent's configured skills:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        skills:
          consumers:
            solver.react.v2.decision.v2.regular:
              allow:
                - public.*
                - automation.automations
            solver.react.v2.decision.v2.job:
              allow:
                - public.*
                - automation.job
```

Bundles may still keep product-specific skills in their own custom skills root.

## Bundle Integration

Use SDK storage directly where the bundle needs automation data:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations import AsyncAutomationStorage

storage = AsyncAutomationStorage(storage_root, user_id=user_id)
automation = await storage.create_automation(
    title="Daily security digest",
    description="Search for new critical CVEs and deliver a PDF summary.",
    schedule_cron="0 8 * * *",
    timezone_name="UTC",
    recurring=True,
)
```

The bundle route layer usually adds:

- user resolution and auth policy
- `storage_root` resolution
- public or operations download URL construction
- Telegram or UI-specific delivery behavior
- Redis background-job enqueueing policy

Those route concerns stay in the bundle. The automation storage, indexes, tool
behavior, job context tools, and artifact materialization stay in the SDK.

## Route Operations

`operations.py` is the reusable route/widget operation layer. A bundle binds
its storage root and user resolution once:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations import operations

operations.configure_automation_operations(
    storage_root_or_error=storage_root_or_error,
    target_user_id=target_user_id,
    bundle_id="my.bundle@1-0",
)
```

After configuration, bundle routes can delegate directly:

```python
await operations.list_automations(entrypoint, user_id=user_id, public=False)
await operations.create_automation(entrypoint, title="Daily digest", user_id=user_id)
await operations.run_automation_now(entrypoint, automation_id=automation_id, user_id=user_id)
await operations.download_execution_artifact(entrypoint, artifact_ref=artifact_ref)
```

The operations module owns generic mechanics:

- user-scoped automation and execution storage
- execution artifact decoration and filtering
- signed public Telegram download URLs
- `BundleBinaryResponse` download payloads
- manual automation job enqueueing
- fresh automation-job ReAct turn execution
- optional Telegram delivery for completed automation executions

Default automation listings return active user-facing definitions only:
`enabled` and `disabled`. Archived revisions and soft-deleted automations remain
addressable through explicit `status="archived"` / `status="deleted"` filters,
but they are not shown in the normal automation list and are not scanned by the due
scheduler.

Automation execution also re-checks automation status when a queued job is picked up. A
scheduled job for an automation that became `disabled`, `archived`, or `deleted` after
enqueue is marked `cancelled` and does not start an agent turn.

One exception is the normal one-shot scheduler path. A non-recurring automation
is disabled immediately after its first due execution is enqueued, so future due
scans do not enqueue it again. The already-queued execution is still runnable
when the execution `source.due_slot` matches the automation metadata
`one_shot_completed_due_slot`.

The app still supplies route authentication, public/operations route aliases,
storage root resolution, and target user selection.

## Due-Automation Scheduler

`due.py` scans enabled automation definitions, computes due slots from cron and
timezone, dedupes queued/running slots, creates queued execution records, and
enqueues background jobs:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations import due, operations

due.configure_due_automations(
    storage_root_or_error=storage_root_or_error,
    automation_operations_module=operations,
)

await due.enqueue_due_automations(entrypoint)
await due.handle_job(entrypoint, job=job)
```

The scheduler reads these bundle config values:

```text
automations.scheduler.max_due_automations_per_tick
automations.scheduler.min_interval_seconds
automations.scheduler.default_queue_label
```

Scheduled and manual jobs both end at `operations.run_automation_execution(...)`.
This keeps the execution lifecycle consistent no matter how the automation was
started.

## Execution Artifacts

Execution artifacts are recoverable by the main assistant and downloadable by
the UI when they are files with user-visible visibility:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.automations import (
    downloadable_execution_artifacts,
    materialize_execution_artifact_for_current_turn,
)
```

The agent recovery flow is:

```text
automations.search_recent_outputs(...)
  -> returns execution_id and artifact_ref
automations.get_automation_execution(execution_id)
  -> confirms exact result and artifacts
automations.materialize_execution_artifact(artifact_ref)
  -> copies the selected file into the current ReAct turn outputs
react.read(["fi:<current_turn>.outputs/..."])
```

The widget/download flow is:

```text
bundle operation endpoint
  -> AsyncAutomationStorage.get_execution(...)
  -> read_execution_artifact_for_download(...)
  -> BundleBinaryResponse(filename, media_type, content)
```

For Telegram Web Apps, the public download route should return:

```text
Content-Disposition: attachment; filename="<file_name>"
Access-Control-Allow-Origin: https://web.telegram.org
```

The SDK provides artifact filtering and bytes resolution. The bundle still signs
short-lived public URLs because signing secrets and public route aliases are
bundle policy.
