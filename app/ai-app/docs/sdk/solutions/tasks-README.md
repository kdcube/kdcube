---
id: ks:docs/sdk/solutions/tasks-README.md
title: "Tasks SDK Solution"
summary: "Reusable scheduled/executable task component for KDCube bundles: task storage, execution journals, artifact recovery, ReAct tools, and skills."
tags: ["sdk", "solutions", "tasks", "scheduler", "executions", "artifacts", "react"]
---

# Tasks SDK Solution

`kdcube_ai_app.apps.chat.sdk.solutions.tasks` is the reusable task component for
bundles that need durable actionable work: saved tasks, schedules, fresh job
executions, execution journals, output artifacts, and model-facing tools.

The bundle owns product policy and routes. The SDK owns the reusable task
mechanics.

## Package Surface

```text
kdcube_ai_app.apps.chat.sdk.solutions.tasks
  storage.py              Markdown + YAML front matter task storage and SQLite FTS task index
  executions_storage.py   Execution journal JSON files and SQLite FTS execution index
  async_storage.py        Async wrappers for file/SQLite task stores
  execution_artifacts.py  Execution artifact indexing, download filtering, materialization
  operations.py           Configurable task CRUD/search/run/download operations
                          for bundle routes and widgets
  due.py                  Configurable due-task scanner and background-job handler
  tools.py                ReAct task-management tools under alias `tasks`
  job_tools.py            ReAct saved-job tools under alias `task_job`
  common.py               Shared tool-context imports
  skills/task/tasks       Built-in `task.tasks` skill
  skills/task/job         Built-in `task.job` skill
```

## Data Model

Task definitions are Markdown assets with YAML front matter:

```text
<storage_root>/tasks/<user_id>/<task_id>.md
<storage_root>/indexes/tasks/<user_id>/tasks.sqlite
```

Execution records are separate from task definitions:

```text
<storage_root>/task_executions/<user_id>/<task_id>/<execution_id>.json
<storage_root>/indexes/task_executions/<user_id>/executions.sqlite
```

The task definition describes what should be done. The execution record
describes what happened during one run: status, summary, logs, result JSON,
conversation/turn ids, and user-visible file artifacts.

## Model-Facing Tools

Main conversation tools:

```python
{
    "module": "kdcube_ai_app.apps.chat.sdk.solutions.tasks.tools",
    "alias": "tasks",
    "use_sk": True,
}
```

Saved-job tools:

```python
{
    "module": "kdcube_ai_app.apps.chat.sdk.solutions.tasks.job_tools",
    "alias": "task_job",
    "use_sk": True,
}
```

`tasks.*` lets the main assistant create, list, edit, link, run, and search
tasks and prior outputs. `task_job.*` is for a fresh job conversation that is
executing one saved task; task id and execution id come from injected runtime
context, not model-authored parameters.

## Skills

The task skills are loaded as SDK solution skills:

```text
task.tasks  -> create/list/update/delete/link tasks and recover execution output
task.job    -> execute one saved task and update its execution journal
```

A bundle enables them through its `skills_descriptor.py`:

```python
REACT_DECISION_SKILLS = [
    "public.*",
    "task.tasks",
]

REACT_JOB_DECISION_SKILLS = [
    "public.*",
    "task.job",
]
```

Bundles may still keep product-specific skills in their own custom skills root.

## Bundle Integration

Use SDK storage directly where the bundle needs task data:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import AsyncTaskStorage

storage = AsyncTaskStorage(storage_root, user_id=user_id)
task = await storage.create_task(
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

Those route concerns stay in the bundle. The task storage, indexes, tool
behavior, job context tools, and artifact materialization stay in the SDK.

## Route Operations

`operations.py` is the reusable route/widget operation layer. A bundle binds
its storage root and user resolution once:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import operations

operations.configure_task_operations(
    storage_root_or_error=storage_root_or_error,
    target_user_id=target_user_id,
    bundle_id="my.bundle@1-0",
)
```

After configuration, bundle routes can delegate directly:

```python
await operations.list_tasks(entrypoint, user_id=user_id, public=False)
await operations.create_task(entrypoint, title="Daily digest", user_id=user_id)
await operations.run_task_now(entrypoint, task_id=task_id, user_id=user_id)
await operations.download_execution_artifact(entrypoint, artifact_ref=artifact_ref)
```

The operations module owns generic mechanics:

- user-scoped task and execution storage
- execution artifact decoration and filtering
- signed public Telegram download URLs
- `BundleBinaryResponse` download payloads
- manual task job enqueueing
- fresh task-job ReAct turn execution
- optional Telegram delivery for completed task executions

The bundle still supplies route authentication, public/operations route aliases,
storage root resolution, and target user selection.

## Due-Task Scheduler

`due.py` scans enabled task definitions, computes due slots from cron and
timezone, dedupes queued/running slots, creates queued execution records, and
enqueues background jobs:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import due, operations

due.configure_due_tasks(
    storage_root_or_error=storage_root_or_error,
    task_operations_module=operations,
)

await due.enqueue_due_tasks(entrypoint)
await due.handle_job(entrypoint, job=job)
```

The scheduler reads these bundle config values:

```text
tasks.scheduler.max_due_tasks_per_tick
tasks.scheduler.min_interval_seconds
tasks.scheduler.default_user_type
```

Scheduled and manual jobs both end at `operations.run_task_execution(...)`.
This keeps the execution lifecycle consistent no matter how the task was
started.

## Execution Artifacts

Execution artifacts are recoverable by the main assistant and downloadable by
the UI when they are files with user-visible visibility:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.tasks import (
    downloadable_execution_artifacts,
    materialize_execution_artifact_for_current_turn,
)
```

The agent recovery flow is:

```text
tasks.search_recent_outputs(...)
  -> returns execution_id and artifact_ref
tasks.get_task_execution(execution_id)
  -> confirms exact result and artifacts
tasks.materialize_execution_artifact(artifact_ref)
  -> copies the selected file into the current ReAct turn outputs
react.read(["fi:<current_turn>.outputs/..."])
```

The widget/download flow is:

```text
bundle operation endpoint
  -> AsyncTaskStorage.get_execution(...)
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
