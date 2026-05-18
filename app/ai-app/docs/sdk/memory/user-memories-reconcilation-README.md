---
id: ks:docs/sdk/memory/user-memories-reconcilation-README.md
title: "User Memories Reconciliation"
summary: "Design contract and SDK agent for user-memory reconciliation: widget-triggered analysis, dry-run proposal jobs, export/review snapshots, versatile-streamer validation, and later transactional application/restore."
tags: ["sdk", "memory", "reconciliation", "maintenance", "versatile-streamer", "postgres"]
keywords: ["memory reconciliation", "duplicate memories", "semantic merge", "stream_with_channels", "ChannelSpec", "structured output", "memory maintenance job"]
see_also:
  - ks:docs/sdk/memory/user-memories-overview-README.md
  - ks:docs/sdk/memory/user-memories-operational-README.md
  - ks:docs/sdk/streaming/channeled-streamer-README.md
---
# User Memories Reconciliation

This is the design contract for reconciliation. Do not reuse the historical
`memory-old` reconciler directly. The v1 reconciler is built for the new
`user_memory_entries/events/aliases` model.

The SDK agent lives in:

```text
kdcube_ai_app/apps/chat/sdk/context/memory/reconciler_agent.py
```

It uses the versatile streamer and returns validated proposed actions. It does
not mutate Postgres; application remains a separate service phase.

The first product surface should be user-controlled:

```text
Create snapshot -> Analyze -> Dry-run proposal job -> Export/review -> Explicit apply -> Restore snapshot if needed
```

Automatic turn-end reconciliation is a later optimization. It should not be the
first shipped behavior because reconciliation can change durable user memory.

## Purpose

Normal writes are incremental and cheap. Reconciliation is a bounded maintenance
process that handles cases the write path should not solve inside every turn:

```text
duplicate memories
near-duplicate memories
stale weak memories
conflicting memories
candidate merges that need structured review
```

Reconciliation must preserve provenance. It should never erase the event trail.

## User-Controlled Lifecycle

The memory widget is the safest first integration point. It gives users a way to
inspect memory health, run a bounded proposal job, export the proposal, and only
then choose whether changes should be applied.

```text
Memory widget
  |
  +-- Create snapshot
  |     store current visible memories for the chosen scope
  |     no LLM
  |     no database mutation beyond the snapshot artifact/index
  |
  +-- Analyze memories
  |     deterministic stats only
  |     no LLM
  |     no database mutation
  |
  +-- Run reconciliation dry run
  |     collect bounded candidates
  |     create/link a first-class snapshot before reasoning
  |     run reconciler agent
  |     validate proposed actions
  |     write proposal artifacts
  |     no database mutation
  |
  +-- Export / review
  |     proposal.json
  |     proposal.md
  |     before.json
  |     current-memories.csv/json when requested
  |
  +-- Apply selected actions
        transactional service phase
        writes audit events
        writes after.json
        can be restored from the linked snapshot
```

The dry-run phase is useful by itself. It can show the user whether there is
anything worth reconciling before any durable state changes.

## Widget UX

The memory widget treats maintenance as a user-directed workflow. The user does
not need to understand the internal agent protocol. They see memory records,
snapshots, analysis, dry-run jobs, and exports.

The main screen should expose these actions:

```text
Memories
  |
  +-- Search / filter / edit memories
  |
  +-- Snapshot
  |     create manual checkpoint
  |     list recent checkpoints
  |     export checkpoint as md/csv/json
  |
  +-- Analyze
  |     deterministic health stats
  |     duplicate/conflict/staleness hints
  |
  +-- Dry-run reconciliation
        submit async job
        refresh job status
        export proposal when done
```

Reconciliation must be submitted, not run inside the widget request:

```text
User clicks "Dry run"
  |
  v
POST memories_widget_reconcile_run
  |
  +-- optional bundle hook normalizes request
  |     on_memory_reconciliation_request(request=...)
  |
  +-- server finds active queued/running job for same user + bundle + scope
  |     |
  |     v
  |   reject with memory_reconciliation_already_running and return active job
  |
  +-- no active job
        |
        v
      create job status=queued in shared store
        |
        v
      acquire active Redis lease for same user + bundle + scope
        |
        v
      enqueue `memory.reconciliation.run` with RedisBackgroundJobStream
        |
        v
      return immediately: accepted=true, job_id, status=queued
```

`memories_widget_reconcile_run` accepts stable platform fields such as
`scope_filter`, `limit`, `reason`, and `agent_type`. Bundle-specific controls
must go into optional JSON object `reconciliation_context`, not into new
top-level platform fields. The SDK stores that object in the job status,
includes it in the background queue payload, and rebinds it at execution time:

```text
bundle_call_context.memory.reconciliation.context
```

Bundles that need request-specific validation, defaults, or derived controls can
override:

```python
async def on_memory_reconciliation_request(self, *, request: dict) -> dict | None:
    context = dict(request.get("reconciliation_context") or {})
    context.setdefault("policy", "strict")
    return {"reconciliation_context": context}
```

This hook is request-side only. It prepares what is persisted and later carried
into the background job; the actual reconciler still runs through
`memory.reconciliation.run`.

The processor delivers that envelope to the bundle's single `@on_job` handler.
Bundles that derive from the memory mixin should dispatch superclass jobs first:

```python
@on_job
async def on_job(self, **kwargs):
    handled = await super().handle_job(**kwargs)
    if handled.get("handled"):
        return handled
    ...
```

`MemoryEntrypointMixin.handle_job(...)` inspects `work_kind`. If it sees
`memory.reconciliation.run`, it executes the stored reconciliation job and
updates the shared job status. If it does not recognize the work kind, it returns
`handled=false` so the bundle can process its own job kinds.

The widget then polls or refreshes the jobs list:

```text
Widget refresh
  |
  v
POST memories_widget_reconcile_jobs
  |
  +-- queued/running  -> show progress state
  +-- succeeded       -> enable proposal export/review
  +-- failed          -> show stored error
  +-- applied         -> show applied result
  +-- restored        -> show restore result
```

The background job updates the shared store at each phase:

```text
queued
  |
  v
running
  |
  +-- create linked snapshot
  |
  +-- collect bounded candidate set
  |
  +-- run reconciler agent
  |
  +-- write proposal.json and proposal.md
  |
  v
succeeded
```

Jobs are indexed in `user_memory_maintenance_artifacts`; larger artifacts live
in the bundle storage that created them. The index includes the originating
`storage_bundle_id`, so an all-memory widget opened from another bundle can list
and export jobs for the same user without assuming the artifacts were written by
the current bundle.

Only one queued/running reconciliation job is allowed per user, bundle, and
scope filter. A second click should not enqueue duplicate work. The server must
reject it and return the active job so the client can refresh the existing
status instead. The first implementation uses both a stored job-status check and
a Redis active-job lease so same-process and cross-process submissions do not
create duplicate reconciliation work.

Restore is also user-driven. It should be previewed before it mutates memory:

```text
User selects snapshot
  |
  v
Export / inspect snapshot
  |
  v
Preview restore
  |
  +-- conflicts found
  |     show conflict report
  |     do not mutate
  |
  +-- user cancels
  |     do not mutate
  |
  +-- user confirms
        |
        v
      apply restore transactionally
        |
        v
      write restore audit events and restore artifact
```

Snapshots are independent of reconciliation. A user can create a snapshot before
manual cleanup, before applying a reconciliation proposal, or simply as a
checkpoint they may want to return to later.

## Memory Snapshots

Snapshots are first-class manual artifacts. They are not only a byproduct of
reconciliation. A user can create a snapshot whenever they want a restorable
memory checkpoint.

```text
Create snapshot
  |
  v
read current user-visible memories for selected scope
  |
  v
write bundle-storage artifacts
  |
  v
register snapshot in user_memory_maintenance_artifacts
```

Suggested artifact layout:

```text
memory/snapshots/YYYY/MM/DD/<snapshot_id>/status.json
memory/snapshots/YYYY/MM/DD/<snapshot_id>/memories.json
memory/snapshots/YYYY/MM/DD/<snapshot_id>/memories.md
memory/snapshots/YYYY/MM/DD/<snapshot_id>/memories.csv
memory/snapshots/YYYY/MM/DD/<snapshot_id>/restore-preview.json      # only after preview
memory/snapshots/YYYY/MM/DD/<snapshot_id>/restore.json              # only after restore
```

Current retention keeps the last few snapshots per user/scope as recovery
checkpoints. Snapshot metadata is listed with `limit`/`offset`; widgets must not
try to render an unbounded job or snapshot history.

Snapshot restore is a separate explicit operation from reconciliation apply:

```text
snapshot restore = restore memories from a chosen saved memory state
reconciliation apply = apply selected reconciler actions
reconciliation undo = restore from the snapshot linked to that job
```

The user can restore from any snapshot, not only the most recent one. Restore
must always be previewed first because restoring an older snapshot can overwrite
newer memory edits or retire memories that did not exist at snapshot time.

The first implementation may ship snapshot creation/list/export before restore
application. Restore application must be transactional and auditable.

## Job Records and Artifacts

Reconciliation jobs should have small status metadata and larger report
artifacts. The job metadata can live in Postgres or in a compact bundle-storage
index. Large payloads should live in bundle storage so local file and S3-backed
deployments use the same abstraction.

Suggested reconciliation artifact layout:

```text
memory/reconciliation/jobs/<job_id>/status.json
memory/reconciliation/jobs/<job_id>/proposal.json
memory/reconciliation/jobs/<job_id>/proposal.md
memory/reconciliation/jobs/<job_id>/after.json        # only after apply
memory/reconciliation/jobs/<job_id>/restore.json      # only after restore
memory/reconciliation/jobs/index.json
```

Every reconciliation job must reference a snapshot:

```json
{
  "job_id": "memrec_...",
  "snapshot_id": "memsnap_...",
  "snapshot_artifact": "memory/snapshots/memsnap_.../memories.json"
}
```

The linked snapshot is mandatory for any job that can later be applied. A job
without a snapshot must remain dry-run only.

Status values:

```text
queued
running
succeeded
failed
applied
restore_conflict
restored
```

The widget should always be able to show recent job status and the stored
proposal/result artifacts. Jobs are listed with `limit`/`offset` and
`has_more`; a widget should page them rather than showing every historical job.

## Export and Restore

Before applying actions, export must be available. The user should be able to
inspect both machine-readable and human-readable output:

```text
proposal.json   exact validated actions, candidate ids, warnings, metadata
proposal.md     readable summary and before/proposed changes
memories.json   linked snapshot data
memories.md/csv linked snapshot exports
```

Snapshot restore is a controlled operation, not a blind overwrite:

```text
Restore snapshot <snapshot_id>
  |
  v
load memories.json
  |
  v
show restore preview/diff
  |
  +-- user cancels -> no mutation
  |
  +-- user confirms -> restore selected rows transactionally
```

Restore must append audit events so the restore is visible in evidence history.
If the restore is tied to a reconciliation job, the default affected set should
be only the memories touched by that job. If the user is restoring a standalone
snapshot, the widget must make the broader scope clear.

## Automatic Scheduling

Automatic reconciliation can exist later, but it should use the same job model
and remain bounded. It must not bypass export/review policy unless a bundle
explicitly enables automatic application.

Run reconciliation opportunistically, similar to compaction.

```text
turn completes or reaches maintenance checkpoint
  |
  v
cheap eligibility check
  |
  +-- too soon / no candidates -> skip
  |
  v
cross-replica lock
  |
  +-- lock held -> skip
  |
  v
bounded reconciliation run
```

The lock can use Redis because it is only a scheduling lease. Redis is not memory
state.

```text
kdcube:memory:reconcile:<schema>:<tenant>:<project>:<user_id>
```

The job must be bounded by candidate count, token budget, and wall time.

The default automatic policy should be proposal-only:

```text
auto_analyze: allowed
auto_dry_run: allowed if configured
auto_apply: disabled by default
```

## Candidate Collection

The collector reads only enough data for review.

```text
user_memory_entries
  WHERE tenant/project/user
    AND status IN active,weakened,unsupported
    AND merged_into_id IS NULL
  ORDER BY tier, salience_score DESC, updated_at DESC
  LIMIT N
        |
        v
candidate packet
  id
  memory
  context (bounded)
  kind
  labels / keywords
  tier and aggregate scores
  confirmation / contradiction / update counts
  updated / confirmed timestamps
  revision
```

Do not send the full memory database to an LLM. Candidate packets should be
small and auditable. Do not include raw event history by default. If a later
reconciliation mode needs evidence, it must request a small explicit evidence
view and keep that separate from the default candidate packet.

The memory fields keep their normal semantics:

```text
memory  = compact trigger first + durable rule/fact/anchor
context = why this exists / provenance / examples only
```

The reconciler should not move applicability conditions out of `memory` into
`context`. If future rewrite/split actions are added, they must preserve this
field contract.

## Agent Protocol

When reconciliation requires reasoning, use the versatile streamer pattern used
by ReAct and the gate agent: one short thinking channel plus one structured
output channel.

```text
system prompt
  |
  v
stream_with_channels()
  |
  +-- channel:thinking
  |     short maintenance status, not private chain-of-thought
  |
  +-- channel:output
        JSON validated into a reconciliation output model
```

Expected channel shape:

```text
<channel:thinking>
Checking a bounded memory candidate set for duplicates and conflicts.
</channel:thinking>

<channel:output>
{
  "actions": [
    {
      "action": "merge",
      "source_memory_id": "mem_...",
      "target_memory_id": "mem_...",
      "confidence": 0.91,
      "reason": "same preference, same labels, repeated wording"
    }
  ]
}
</channel:output>
```

The output channel is validated with `MemoryReconciliationOut` and then filtered
by `validate_reconciliation_output(...)` before any database mutation.

SDK usage:

```python
from kdcube_ai_app.apps.chat.sdk.context.memory import (
    MemorySearchRequest,
    memory_reconciler_stream,
)

results = await store.search(
    MemorySearchRequest(scope=scope, mode="hotset", status="active", limit=40)
)

out, channels, meta = await memory_reconciler_stream(
    svc,
    candidates=results,
    reason="periodic memory maintenance",
)
```

`out.actions` contains only locally validated proposals. Unknown ids, malformed
actions, and low-confidence merge proposals are dropped into `out.warnings`.

## Structured Actions

Initial action set:

```text
merge
  source_memory_id
  target_memory_id
  merged_memory
  merged_context
  confidence
  reason

squash
  source_memory_ids[]
  target_memory_id
  merged_memory
  merged_context
  confidence
  reason

weaken
  memory_id
  reason

retire
  memory_id
  reason

no_op
  confidence
  reason
```

Future actions may include split/rewrite, but those should be added only after
we have UI review semantics.

## Application Phase

The agent does not mutate the database directly. The service validates the
structured output, then applies each action transactionally.

```text
validated action
  |
  v
BEGIN
  SELECT target/source FOR UPDATE
  verify same tenant/project/user
  verify source is not already merged
  update aggregate row
  append user_memory_events row
COMMIT
```

Merge should look like:

```text
target.memory/context = reconciler-proposed merged text
source.status = merged
source.merged_into_id = target.id
append event_type=merge to source
append event_type=merge_target_updated to target
```

Squash is the first-class form for many-to-one cleanup. It must rewrite the
target once, rebuild the target embedding, append one target squash event
containing all source ids, and mark each source row as `merged` with
`merged_into_id=target.id`. Normal search filters `merged_into_id IS NULL`, so
merged source rows do not reappear in future memory queries.

The source rows remain available only through explicit historical/status
inspection. They are not part of normal active memory serving.

Apply must check revisions from `before.json` before changing rows. If a memory
was edited after the proposal was generated, the action should become a conflict
instead of silently overwriting newer user work.

Every applied action needs an idempotency key, for example:

```text
memory_reconciler:<job_id>:<action>:<source_id>:<target_id>:<revision>
```

This is separate from the memory canonical key. The canonical key deduplicates
memory identity; the idempotency key deduplicates repeated application attempts.

## Manual Memories

Manual memories may not have `conversation_id` or `turn_id`. They are still
eligible for reconciliation.

```text
manual memory event
  conversation_id = ""
  turn_id         = ""
  source.origin   = "manual"
```

The reconciler must not assume every memory came from a chat turn.

## Non-Goals

- No broad summarization of all memories in the chat turn.
- No silent deletes.
- No Redis cache as memory state.
- No direct reuse of `memory-old` code.
- No LLM mutation without structured output validation.
- No automatic apply before widget review/export/restore semantics are wired.
