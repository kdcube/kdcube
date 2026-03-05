---
id: ks:docs/sdk/agents/react/react-turn-workspace-README.md
title: "ReAct Turn Workspace"
summary: "Per-turn execution workspace (work/out) and how it differs from timeline, turn log, and sources pool."
tags: ["sdk", "agents", "react", "workspace", "execution"]
keywords: ["turn workspace", "workdir", "outdir", "exec-workspace", "execution snapshot", "timeline.json"]
see_also:
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/source-pool-README.md
  - ks:docs/sdk/agents/react/external-exec-README.md
---
# ReAct Turn Workspace

The **turn workspace** is a **per‑turn execution sandbox** created by the processor.
It is **not** the source of truth for conversation state. It is a diagnostic artifact that
captures what happened during one turn.

This workspace is recreated on every turn. It exists to:
- stage inputs (attachments, fetched files)
- store tool outputs and logs
- give operators a forensics snapshot when debugging a turn

## How the workspace is created

On each turn, `ContextBrowser._ensure_workspace()` creates:
- `.../ctx_v2_<id>/work`
- `.../ctx_v2_<id>/out`

It also sets:
- `WORKDIR` and `OUTPUT_DIR` environment variables
- `WORKDIR_CV` / `OUTDIR_CV` contextvars (for tool runtime plumbing)

Workspace root is resolved by `get_exec_workspace_root()` and typically comes from:
- `EXEC_WORKSPACE_ROOT` (preferred)
- fallback to `HOST_EXEC_WORKSPACE_PATH` in host mode
- default `/exec-workspace` in Docker or `/tmp` on host

## What is inside the workspace

The workspace is broader than “files written by tools”. It can include:
- Turn attachments provided by the user (staged into `work/` or `out/`)
- Files produced during the turn (reports, images, zips, etc.)
- Execution logs (tool logs, code‑exec logs, stdout/stderr)
- Materialized artifacts referenced by the agent during the turn
  (e.g., files pulled from earlier turns, or fetched documents)
- `out/timeline.json` — a compact snapshot of the timeline **plus** sources pool

This data is **turn‑local**. It is not used for future turn reconstruction.

## Hosted artifacts happen immediately

When a tool produces a **file artifact** with `visibility=external`, the React tool
layer **hosts it immediately** (uploads to the artifact store) and emits the hosted
payload into the turn stream. This happens *during the turn* and does **not** depend
on the workspace snapshot.

Implementation: `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/external.py`
(see `host_artifact_file(...)` + `emit_hosted_files(...)`).

### Implication
Even if you **disable workspace persistence**, hosted artifacts are still available
via their logical paths (`fi:`) and hosted URIs.

## What is the real source of truth?

Conversation state is stored elsewhere:
- **Timeline** (`artifact:conv.timeline.v1`) — ordered blocks + metadata
- **Sources pool** (`artifact:conv:sources_pool`)
- **Turn log** — ordered blocks for the single turn
- **Messages / attachments / files** — persisted in conversation storage

The workspace is a **snapshot of execution**, not the canonical record.
Do not build retrieval features from workspace content.

## Persisting the workspace (optional)

At the end of a turn, some bundles call:

```
await react.persist_workspace()
```

This writes a **zip snapshot** of `work/` and `out/` into conversation storage
(`executions/.../turn_id/run_id/...`). This is useful for diagnostics but is not
required for conversation correctness.

You can disable this globally with:

```
REACT_PERSIST_WORKSPACE=0
```

Default is enabled (`1`). When disabled, `react.persist_workspace()` becomes a no‑op.

## Relationship to external execution

External exec uses a **separate snapshot path** (see `external-exec-README.md`):
- It builds a *lightweight* snapshot for remote runners.
- It merges only select outputs back into the live workspace.

That flow is related to execution, but **distinct** from the turn workspace snapshot.
