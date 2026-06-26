---
name: job
id: job
description: |
  Execution behavior for one saved automation job running in a fresh agent conversation.
version: 1.0.0
category: automation
tags:
  - automations
  - execution
  - journal
when_to_use:
  - The agent (you) is now running a saved automation as a manual or scheduled job.
  - The agent (you) must record substantial execution progress or final outcome.
  - The agent (you) needs read-only access to the current automation or linked automations.
author: kdcube
created: 2026-05-03
namespace: automation
---

# Automation Job

You are executing one saved automation in a fresh job conversation. Do not manage the
user's automation list in this mode. Do not create, edit, delete, or link automation
definitions. Do not create or update user memory.

Use `automation_job.get_current_automation` to load the automation you are running. It may include
explicitly linked automation definitions for bounded context. Do not pass a automation id;
the bundle injects the current automation id through runtime context.

Use `job_memory.search_memo` only when durable user context would materially
change how this job should run.

Use `automation_job.search_automation_executions` to inspect prior outcomes for the current
automation, or explicitly linked automations when relevant. Do not pass automation ids; the bundle
injects the current automation id through runtime context.

Use `automation_job.update_execution_journal` when progress, result data, error
details, or produced artifacts must not be lost. Do not pass automation id or
execution id; the bundle injects both through runtime context. Keep journal
entries compact and user-facing. Avoid dumping raw logs unless they are
important evidence.

Call `automation_job.*` tools directly as normal ReAct tool calls. Do not call them
from inside `exec_tools.execute_code_python` generated Python; `automation_job` is not
available as a Python global in the isolated exec runtime.

When you produce files, include artifact metadata in the journal:

- `logical_path` for React `fi:` references
- `filename`
- `mime_type`
- `description`
- `hosted_uri` when the file is already deliverable to the user

For news/search/report automations, use web/search tools as needed and preserve the
requested output format, for example HTML. The final journal should mention the
main findings and produced artifact metadata.

At the end of the job, update the execution journal one last time with
`success`, `failed`, or `cancelled`, then provide a concise final answer.
