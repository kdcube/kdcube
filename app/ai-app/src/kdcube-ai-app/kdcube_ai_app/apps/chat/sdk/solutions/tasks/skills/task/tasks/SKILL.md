---
name: tasks
id: tasks
description: |
  Create, inspect, edit, archive, delete, run, and search scheduled or executable task assets.
version: 1.0.0
category: task
tags:
  - tasks
  - scheduler
  - execution
when_to_use:
  - The user asks to create, list, disable, delete, restore, or search tasks.
  - The user asks to schedule recurring work.
  - The user asks for a recurring news/search/report generation task.
  - A chat, webhook, Telegram, or other channel message asks the assistant to track actionable work.
  - The user describes an email-processing automation that should run later.
author: kdcube
created: 2026-05-02
namespace: task
---

# Tasks

Tasks are executable assets. They are not user memory.

Use `tasks.list_tasks` before creating a task when the request may duplicate an existing task.
Use `tasks.search_tasks` when the user asks to edit, delete, connect, or find a specific existing task.
Use `tasks.get_task` to inspect the full task definition and recent execution history before changing or explaining it.
Use `tasks.create_task` for actionable work the assistant may later execute or schedule.
Use `tasks.update_task` for task definition changes after identifying the exact task.
Use `tasks.delete_task` for removal requests; it soft-deletes by default.
Use `tasks.set_task_status` for disable, archive, delete, restore, and enable requests.
Use `tasks.link_task` when tasks have follow-up, dependency, blocker, parent/child, or related-task semantics.
Use `tasks.list_task_executions` when the user asks whether a task ran, how it ended, or what happened recently.
Use `tasks.search_task_executions` when the user asks for prior run output, failures, logs, or produced files.
Use `tasks.search_recent_outputs` when the user refers to a prior delivered
report, spreadsheet, file, job result, or says "it", "that report", or "the file
you sent" and the item is not on the current chat timeline.
Use `tasks.get_task_execution` after list/search returns an execution id and you
need the exact execution result, job conversation id, turn id, or artifact
metadata.
Use `tasks.materialize_execution_artifact` after `search_recent_outputs` or
`get_task_execution` returns the artifact to copy it into the current React turn
before reading, editing, or regenerating from it. Pass only the returned
`artifact_ref`; do not split it into execution id, task id, filename, or path
arguments.

Only pass task ids, execution ids, or memory ids after a list/search/get tool has
returned them. Do not invent ids. Conversation ids, execution conversation ids,
source labels, hard-delete flags, task execution ids, and execution journal ids
are runtime/UI concerns, not model-authored task-management parameters.

When creating a task, keep the title short, put execution details in the
description, and preserve schedule/context fields explicitly when the user
provides them. `schedule.recurring` is a boolean. Use `true` for repeating
tasks and `false` for one-shot scheduled tasks that should disable themselves
after the first due run. Do not represent recurrence as strings or numbers.
Manual and due executions should use the shared fresh-job execution loop. Each
run creates a fresh `task_job_*` conversation and records the result in task
execution storage.

Execution records are separate from task definitions. Store only substantial run
outcomes there: status changes, user-facing summaries, compact log excerpts,
structured result data, and artifacts that must be available after the turn.
Artifact references may start as React `fi:` logical paths; execution-journal
tooling should later resolve and rehost them into bundle storage before
notifying the configured delivery channel.

The main conversational agent does not write execution records directly. Manual
and scheduled task execution must go through the fresh job-execution loop, where
the job agent writes progress through `task_job.update_execution_journal`.

## External Job Results And "This"

The main chat timeline is not the only source of user-visible facts. Saved-task
jobs run in separate job conversations and can send files or summaries back to
the configured delivery channel. The user may then ask the main assistant about
"this", "that report", "the spreadsheet", or "the file you sent" even though the
object is not present on the main chat timeline.

Each saved-task execution records the job `conversation_id` and `turn_id`. The
execution record is an index row; the artifact source of truth is the job turn
timeline in conversation storage.

Resolution rule:

- first inspect whether a clear referent is already visible on the current main
  timeline; if yes, you may use that visible object
- if there is no clear visible referent, or if an external job result may have
  happened after the visible object, call `tasks.search_recent_outputs`
- when there is a possible visible referent, pass a focused query and
  `completed_after_iso` set after the visible object's timestamp so later job
  outputs can win when appropriate
- call `tasks.get_task_execution` for the selected execution before making claims
  about its result or artifacts
- for a selected artifact, follow its `access` field exactly

Artifact access flow:

1. Call `tasks.materialize_execution_artifact` with only
   `access.materialize.params.artifact_ref`.
2. Use the returned `current_turn.logical_path` with `react.read`.
3. For binary artifacts such as XLSX, `react.read` may return metadata; use
   `current_turn.physical_path` from code/rendering tools to inspect or modify
   the file.

Do not use `react.pull` or `react.checkout` for external job outputs. The
materialized file is already copied into the current turn outputs, so
`react.read` is the correct next step.

Do not invent artifact paths, conversation ids, or task execution ids. Do not
use source/provenance metadata as a React path. For artifact materialization, copy the
opaque `artifact_ref` exactly as returned.

## Creation Prerequisites

Create a task only when there is enough context to execute it later. If required
details are missing, ask a short follow-up instead of saving a vague task.

For email-processing tasks:

- the user must say which concrete connected email address, mailbox, or label
  should be processed; "my Gmail" is not enough for a saved task because users
  can connect more than one Gmail account
- the user must state the condition/rule that matches messages
- the task description must say what details to report, for example who sent it,
  when it arrived, what matched, why it matters, and which message should be sent
  back through the configured delivery channel
- if no connected email account is known, ask the user to connect email in the
  settings UI before creating the executable task
- if email accounts exist but the user did not name the concrete email address,
  ask which connected email address to use before creating the executable task
- do not ask for email passwords in chat or external channels
- do not create the executable email task until the account/mailbox target and
  intended processing rule are clear

For search/news/report tasks:

- the user must state the topic, cadence or due time, expected format, and
  delivery channel
- if the user asks for a one-time scheduled run, set `recurring=false`; if they
  ask for repeating work, set `recurring=true`
- if the user asks for HTML, preserve that output format in the task description
- if source quality matters, record the source constraints or ask a follow-up

Before saving any task, consider the currently available tools and skills. If
the task requires a capability that is not available in this React turn, ask for
the missing prerequisite or explain that the task cannot be made executable yet.
