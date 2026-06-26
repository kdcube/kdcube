---
name: automations
id: automations
description: |
  Create, inspect, edit, archive, delete, run, and search scheduled or executable automation assets.
version: 1.0.0
category: automation
tags:
  - automations
  - scheduler
  - execution
when_to_use:
  - The user asks to create, list, disable, delete, restore, or search automations.
  - The user asks to schedule recurring work.
  - The user asks for a recurring news/search/report generation automation.
  - A chat, webhook, Telegram, or other channel message asks the assistant to track actionable work.
  - The user describes an email-processing automation that should run later.
author: kdcube
created: 2026-05-02
namespace: automation
---

# Automations

Automations are executable assets. They are not user memory.

Use `automations.list_automations` before creating a automation when the request may duplicate an existing automation.
Use `automations.search_automations` when the user asks to edit, delete, connect, or find a specific existing automation.
Use `automations.get_automation` to inspect the full automation definition and recent execution history before changing or explaining it.
Use `automations.create_automation` for actionable work the assistant may later execute or schedule.
Use `automations.update_automation` for automation definition changes after identifying the exact automation.
Use `automations.delete_automation` for removal requests; it soft-deletes by default.
Use `automations.set_automation_status` for disable, archive, delete, restore, and enable requests.
Use `automations.link_automation` when automations have follow-up, dependency, blocker, parent/child, or related-automation semantics.
Use `automations.list_automation_executions` when the user asks whether a automation ran, how it ended, or what happened recently.
Use `automations.search_automation_executions` when the user asks for prior run output, failures, logs, or produced files.
Use `automations.search_recent_outputs` when the user refers to a prior delivered
report, spreadsheet, file, job result, or says "it", "that report", or "the file
you sent" and the item is not on the current chat timeline.
Use `automations.get_automation_execution` after list/search returns an execution id and you
need the exact execution result, job conversation id, turn id, or artifact
metadata.
Use `automations.materialize_execution_artifact` after `search_recent_outputs` or
`get_automation_execution` returns the artifact to copy it into the current React turn
before reading, editing, or regenerating from it. Pass only the returned
`artifact_ref`; do not split it into execution id, automation id, filename, or path
arguments.

Only pass automation ids, execution ids, or memory ids after a list/search/get tool has
returned them. Do not invent ids. Conversation ids, execution conversation ids,
source labels, hard-delete flags, automation execution ids, and execution journal ids
are runtime/UI concerns, not model-authored automation-management parameters.

When creating a automation, keep the title short, put execution details in the
description, and preserve schedule/context fields explicitly when the user
provides them. `schedule.recurring` is a boolean. Use `true` for repeating
automations and `false` for one-shot scheduled automations that should disable themselves
after the first due run. Do not represent recurrence as strings or numbers.
Manual and due executions should use the shared fresh-job execution loop. Each
run creates a fresh `automation_job_*` conversation and records the result in automation
execution storage.

Execution records are separate from automation definitions. Store only substantial run
outcomes there: status changes, user-facing summaries, compact log excerpts,
structured result data, and artifacts that must be available after the turn.
Artifact references may start as React `fi:` logical paths; execution-journal
tooling should later resolve and rehost them into bundle storage before
notifying the configured delivery channel.

The main conversational agent does not write execution records directly. Manual
and scheduled automation execution must go through the fresh job-execution loop, where
the job agent writes progress through `automation_job.update_execution_journal`.

## External Job Results And "This"

The main chat timeline is not the only source of user-visible facts. Saved-automation
jobs run in separate job conversations and can send files or summaries back to
the configured delivery channel. The user may then ask the main assistant about
"this", "that report", "the spreadsheet", or "the file you sent" even though the
object is not present on the main chat timeline.

Each saved-automation execution records the job `conversation_id` and `turn_id`. The
execution record is an index row; the artifact source of truth is the job turn
timeline in conversation storage.

Resolution rule:

- first inspect whether a clear referent is already visible on the current main
  timeline; if yes, you may use that visible object
- if there is no clear visible referent, or if an external job result may have
  happened after the visible object, call `automations.search_recent_outputs`
- when there is a possible visible referent, pass a focused query and
  `completed_after_iso` set after the visible object's timestamp so later job
  outputs can win when appropriate
- call `automations.get_automation_execution` for the selected execution before making claims
  about its result or artifacts
- for a selected artifact, follow its `access` field exactly

Artifact access flow:

1. Call `automations.materialize_execution_artifact` with only
   `access.materialize.params.artifact_ref`.
2. Use the returned `current_turn.logical_path` with `react.read`.
3. For binary artifacts such as XLSX, `react.read` may return metadata; use
   `current_turn.physical_path` from code/rendering tools to inspect or modify
   the file.

Do not use `react.pull` or `react.checkout` for external job outputs. The
materialized file is already copied into the current turn outputs, so
`react.read` is the correct next step.

Do not invent artifact paths, conversation ids, or automation execution ids. Do not
use source/provenance metadata as a React path. For artifact materialization, copy the
opaque `artifact_ref` exactly as returned.

## Creation Prerequisites

Create a automation only when there is enough context to execute it later. If required
details are missing, ask a short follow-up instead of saving a vague automation.

For email-processing automations:

- the user must say which concrete connected email address, mailbox, or label
  should be processed; "my Gmail" is not enough for a saved automation because users
  can connect more than one Gmail account
- the user must state the condition/rule that matches messages
- the automation description must say what details to report, for example who sent it,
  when it arrived, what matched, why it matters, and which message should be sent
  back through the configured delivery channel
- if no connected email account is known, ask the user to connect email in the
  settings UI before creating the executable automation
- if email accounts exist but the user did not name the concrete email address,
  ask which connected email address to use before creating the executable automation
- do not ask for email passwords in chat or external channels
- do not create the executable email automation until the account/mailbox target and
  intended processing rule are clear

For search/news/report automations:

- the user must state the topic, cadence or due time, expected format, and
  delivery channel
- if the user asks for a one-time scheduled run, set `recurring=false`; if they
  ask for repeating work, set `recurring=true`
- if the user asks for HTML, preserve that output format in the automation description
- if source quality matters, record the source constraints or ask a follow-up

Before saving any automation, consider the currently available tools and skills. If
the automation requires a capability that is not available in this React turn, ask for
the missing prerequisite or explain that the automation cannot be made executable yet.
