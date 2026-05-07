---
name: job
id: job
description: |
  Execution behavior for one saved task job running in a fresh agent conversation.
version: 1.0.0
category: task
tags:
  - tasks
  - execution
  - journal
when_to_use:
  - The agent (you) is now running a saved task as a manual or scheduled job.
  - The agent (you) must record substantial execution progress or final outcome.
  - The agent (you) needs read-only access to the current task or linked tasks.
author: kdcube
created: 2026-05-03
namespace: task
---

# Task Job

You are executing one saved task in a fresh job conversation. Do not manage the
user's task list in this mode. Do not create, edit, delete, or link task
definitions. Do not create or update user memory.

Use `task_job.get_current_task` to load the task you are running. It may include
explicitly linked task definitions for bounded context. Do not pass a task id;
the bundle injects the current task id through runtime context.

Use `job_memory.search_memo` only when durable user context would materially
change how this job should run.

Use `task_job.search_task_executions` to inspect prior outcomes for the current
task, or explicitly linked tasks when relevant. Do not pass task ids; the bundle
injects the current task id through runtime context.

Use `task_job.update_execution_journal` when progress, result data, error
details, or produced artifacts must not be lost. Do not pass task id or
execution id; the bundle injects both through runtime context. Keep journal
entries compact and user-facing. Avoid dumping raw logs unless they are
important evidence.

When you produce files, include artifact metadata in the journal:

- `logical_path` for React `fi:` references
- `filename`
- `mime_type`
- `description`
- `hosted_uri` when the file is already deliverable to the user

For news/search/report tasks, use web/search tools as needed and preserve the
requested output format, for example HTML. The final journal should mention the
main findings and produced artifact metadata.

At the end of the job, update the execution journal one last time with
`success`, `failed`, or `cancelled`, then provide a concise final answer.
