---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-model-README.md
title: "ReAct Workspace Model"
summary: "Authoritative agent-facing contract for the sparse per-turn ReAct workspace, conv:fi refs, git/projects project state, files produced artifacts, git/snapshots state snapshots, and pull/checkout/read/search boundaries."
status: active
tags: ["sdk", "agents", "react", "workspace", "pull", "checkout", "announce", "artifacts"]
updated_at: 2026-07-14
keywords:
  [
    "react workspace",
    "sparse workspace",
    "conv:fi",
    "git/projects",
    "files",
    "git/snapshots",
    "react.pull",
    "react.checkout",
    "react.rg",
    "materialization authorization",
    "bound user scope",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-realm-refs-and-workspace-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/git-backed-workspace-engineering-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-lifecycle-and-distribution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
---

# ReAct Workspace Model

This page describes the agent-facing ReAct workspace. The ref grammar itself is
defined in
[ReAct Realm Refs And Workspace Paths](../react-realm-refs-and-workspace-paths-README.md).

The workspace is sparse:

```text
each turn starts with an empty OUTPUT_DIR/turn_<current>/
visible refs persist across turns
local bytes do not persist across turns
```

If code, `react.rg`, `react.patch`, rendering tools, or file inspection need
bytes this turn, the bytes must be present in the current `[WORKSPACE]` LOCAL
tree. If they are not there, materialize them first with `react.pull` or
`react.checkout`.

## Current Physical Layout

```text
OUTPUT_DIR/
  turn_<current>/
    git/projects/<project_scope>/...     # editable durable project state
    files/<artifact_scope>/...           # produced artifacts and deliverables
    git/snapshots/<snapshot_scope>/...    # story/canvas/wizard state snapshots
    attachments/...                      # user uploads for this turn
    external/<kind>/attachments/<event_id>/...
  conv_<other_conversation>/
    turn_<id>/...                        # pulled cross-conversation material
  logs/
  timeline.json
```

Only `OUTPUT_DIR`-relative paths are model-facing physical paths. Never expose
host absolute paths, container implementation paths, or storage-provider URLs as
tool input paths.

## Logical Refs

The same content is addressed across turns by `conv:fi:` logical refs:

```text
conv:fi:turn_<id>.git/projects/<project_scope>/<path>
conv:fi:turn_<id>.files/<artifact_scope>/<path>
conv:fi:turn_<id>.git/snapshots/<snapshot_scope>/<path>
conv:fi:turn_<id>.user.attachments/<name>
conv:fi:turn_<id>.external.<kind>.attachments/<event_id>/<name>

conv:fi:conv_<conversation_id>.turn_<id>.files/<artifact_scope>/<path>
```

`conv:` is the owner namespace. `conv:fi:` is the ReAct file/artifact family.
`conv_<conversation_id>` is a body segment for cross-conversation reads; it is
not a namespace and must stay inside the body.

## What Goes Where

| Area | Use it for | Do not use it for |
| --- | --- | --- |
| `git/projects/` | Durable project/app/repo state that should survive as editable workspace state. | One-off reports or generated downloads. |
| `files/` | Produced artifacts and deliverables: PDFs, DOCX, PPTX, XLSX, CSV, HTML reports, archives, diagnostics, render sources. | Durable project source trees. |
| `git/snapshots/` | Story, wizard, canvas, board, or workflow state snapshots. | User downloads unless a snapshot is explicitly exported as a file. |
| `attachments/` | Current user uploads. | Assistant-produced output. |
| `external/` | Rehosted event/domain attachments or evidence. | Direct user-authored project state. |

The old mental model "workspace files versus outputs" is not current. Use
`git/projects` for project state and `files` for produced artifacts.

## `[WORKSPACE]` In ANNOUNCE

`[WORKSPACE]` is rebuilt every round and has two roles:

- **LOCAL**: what is actually materialized under `OUTPUT_DIR` in this worker
  for this turn.
- **REMOTE git branch**: durable project scopes that can be pulled from the
  conversation git lineage; they are not local until pulled or checked out.

Example:

```text
[WORKSPACE]
  current_turn_root: turn_2026-07-04-09-00-00-000/

  LOCAL - materialized on disk THIS turn
  turn_2026-07-04-09-00-00-000/   (current turn · EDITABLE)
    git/projects/
      site/                       checked out · MODIFIED this turn
    files/
      report/report.pdf
    git/snapshots/
      story/main.json
    attachments/
      requirements.xlsx

  turn_2026-07-03-18-30-02-111/   (pulled reference · READ-ONLY)
    git/projects/
      analytics_dashboard/

  REMOTE git branch - project scopes you can pull (NOT local until pulled)
  latest committed turn: turn_2026-07-03-18-30-02-111
    git/projects/site             [editable in current turn]
    git/projects/analytics_dashboard [pulled · read-only]
    git/projects/data_pipeline
```

If a ref is visible in timeline but absent from LOCAL, it is not on disk now.
Pull it before local search or byte-level work.

## Who Requests And Who Materializes

ReAct decides which locator to request. The runtime decides which identity and
authority apply to that request. These are separate decisions.

| Step | Owner | Security meaning |
| --- | --- | --- |
| Propose ref | ReAct/model | Untrusted locator input; it grants no access. |
| Bind scope | Ingress/runtime | Tenant, project, actor, user, and authority are established outside model output. |
| Resolve | Conversation store, git workspace service, or owner rehoster | Lookup executes under the bound user/authority scope. |
| Materialize | Trusted workspace service | Successfully resolved bytes are copied into the current workspace. |
| Execute | Generated-code runtime | Code reads only the resulting physical workspace view. |

For `conv:fi:` history, a ref may name a conversation and turn. The context
lookup still receives the runtime-bound `user_id`, so the effective lookup is
`bound user + requested conversation + requested turn`. Git-backed workspace
lineage similarly remains rooted at
`tenant/project/bound user/conversation`.

For external owner refs, the namespace rehoster resolves under the carried
request identity and its own owner policy. If resolution yields no in-scope
object, no bytes are placed in the workspace.

This rule holds for compromised model behavior: changing a ref string changes
the requested locator, while the bound user and authority remain unchanged.

## Pull

`react.pull` materializes bytes into the current turn worker. It accepts:

- `conv:fi:` historical artifact refs;
- registered external owner refs such as `mem:...`, `task:...`, or `cnv:...`.

It returns rows with at least:

```json
{
  "source_ref": "task:issue:ticket_123",
  "logical_path": "conv:fi:turn_2026-07-04-09-00-00-000.files/task/ticket_123.json",
  "physical_path": "turn_2026-07-04-09-00-00-000/files/task/ticket_123.json",
  "mime": "application/json",
  "snapshot": true
}
```

For owner refs, use the returned paths. Do not derive a `conv:fi:` ref by hand.

## Checkout

`react.checkout` is narrower than pull. It is only for turning historical
project state into the current editable project tree.

Valid checkout refs:

```text
conv:fi:turn_<id>.git/projects/<project_scope>
conv:fi:conv_<conversation_id>.turn_<id>.git/projects/<project_scope>
```

Invalid checkout refs:

```text
mem:...
task:...
cnv:...
conv:ev:...
conv:fi:turn_<id>.files/report.pdf
conv:fi:turn_<id>.git/snapshots/story/main.json
```

After checkout, edit the current physical path:

```text
turn_<current>/git/projects/<project_scope>/...
```

Do not edit the historical source path.

## Read, Search, Patch, Execute, Render

```text
react.read
  reads logical refs and materialized file refs
  accepts conv:ar, conv:tc, conv:so, conv:ws, conv:su, conv:ev, conv:fi

react.rg
  searches only local materialized physical paths in the current worker
  examples: turn_<current>/git/projects/site, turn_<current>/files/report

react.patch
  patches current-turn physical paths
  preferred area for project edits: turn_<current>/git/projects/<project_scope>/...

exec code
  reads/writes current-turn physical paths under OUTPUT_DIR

rendering tools
  write produced files under turn_<current>/files/<artifact_scope>/...
```

`react.rg` does not search unpulled historical refs or external namespaces.
`react.read` is for context materialization; `react.rg`, exec, patch, and
rendering need local bytes.

## Current-Turn Write Rules

Use these physical paths:

```text
Project/app source:
  turn_<current>/git/projects/<project_scope>/...

Produced report or download:
  turn_<current>/files/<artifact_scope>/...

Story/canvas/wizard state:
  turn_<current>/git/snapshots/<snapshot_scope>/...
```

`react.write` may take shorter current-turn paths according to its tool doc,
but the resulting logical refs must land in the same namespaces above.

## Cross-Conversation Material

Cross-conversation refs preserve the `conv_<conversation_id>` body segment:

```text
conv:fi:conv_<conversation_id>.turn_<id>.files/report.pdf
```

When pulled, cross-conversation material lands under:

```text
conv_<conversation_id>/turn_<id>/files/report.pdf
```

That prefix distinguishes the source conversation in local physical paths.

## Quick Debug Checklist

When an agent says it cannot find or open content:

1. Check whether the visible ref is logical (`conv:fi:...`) or physical
   (`turn_.../...`).
2. Check `[WORKSPACE]` LOCAL. If the physical path is absent, pull or checkout.
3. If the ref is `mem:`, `task:`, or `cnv:`, call `react.pull` and use the
   returned `conv:fi:`/physical paths.
4. If the desired edit is project state, checkout a `conv:fi:...git/projects`
   ref or write into `turn_<current>/git/projects/...`.
5. If the desired output is a downloadable/report artifact, write into
   `turn_<current>/files/...`.
