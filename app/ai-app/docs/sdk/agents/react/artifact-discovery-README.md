---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
title: "Artifact Discovery"
summary: "How ReAct discovers, reads, pulls, searches, and checks out conversation-owned refs and external owner refs without confusing logical refs, physical paths, and produced artifacts."
tags: ["sdk", "agents", "react", "artifacts", "discovery", "pull", "read"]
updated_at: 2026-07-04
keywords:
  [
    "artifact discovery",
    "conv:fi",
    "react.pull",
    "react.read",
    "react.checkout",
    "external owner refs",
    "materialization",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/artifact-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/artifact-resolution-and-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
---
# Artifact Discovery

This page explains how an agent should move from a visible ref to usable
content. The grammar is defined in
[Harness References And Workspace Paths](../../../runtime/harness/workspace/references-and-paths-README.md).

## Ref Classes

| Ref class | Examples | What to do |
| --- | --- | --- |
| Conversation text/control records | `conv:ar:conv_<conversation_id>.turn_1.user.prompt`, `conv:tc:conv_<conversation_id>.turn_1.tc_a.result`, `conv:ev:conv_<conversation_id>.turn_1.events/...` | Use `react.read` when exact content is needed. |
| Conversation file/artifact refs | `conv:fi:conv_<conversation_id>.turn_1.files/report.pdf`, `conv:fi:conv_<conversation_id>.turn_1.git/projects/app/main.py` | Pull if bytes must exist locally; read for context previews/contents. |
| External owner refs | `mem:mem_123`, `task:issue:ticket_123`, `cnv:main@7` | Use `react.pull`; then use returned `conv:fi:` and physical paths. |
| Physical current-turn paths | `turn_1/files/report.pdf`, `turn_1/git/projects/app/main.py` | Use with exec, rendering, patch, or rg when the path is local this turn. |

The agent should not derive physical paths from external owner refs. Owner refs
are opaque until `react.pull` returns concrete paths.

## Discovery Loop

```text
visible timeline / ANNOUNCE / tool result
        |
        v
identify ref class
        |
        +-- conv:ar / conv:tc / conv:so / conv:ws / conv:su / conv:ev
        |       -> react.read when exact record content is needed
        |
        +-- conv:fi
        |       -> react.read for content/projection
        |       -> react.pull if local bytes are needed this turn
        |       -> react.checkout only for
        |          conv:fi:conv_<conversation_id>.<turn>.git/projects/...
        |
        +-- mem / task / cnv / other registered owner namespace
                -> react.pull
                -> continue with returned conv:fi logical_path and physical_path
```

## Materialization Results

`react.pull` returns rows that bind the original ref to current-turn material:

```json
{
  "source_ref": "mem:mem_123",
  "logical_path": "conv:fi:conv_<conversation_id>.turn_2026-07-04-09-00-00-000.files/memory/mem_123.json",
  "physical_path": "turn_2026-07-04-09-00-00-000/files/memory/mem_123.json",
  "mime": "application/json",
  "snapshot": true,
  "object_ref": "mem:mem_123"
}
```

Use the returned values. Do not guess a `conv:fi:` path from the source ref.

## Workspace Path Rules

Current physical namespaces:

```text
turn_<id>/git/projects/...     editable durable project state
turn_<id>/files/...            produced artifacts and deliverables
turn_<id>/git/snapshots/...    story/canvas/wizard state snapshots
turn_<id>/attachments/...      current user uploads
turn_<id>/external/...         rehosted event/domain attachments
```

Logical equivalents:

```text
conv:fi:conv_<conversation_id>.turn_<id>.git/projects/...
conv:fi:conv_<conversation_id>.turn_<id>.files/...
conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/...
conv:fi:conv_<conversation_id>.turn_<id>.user.attachments/...
conv:fi:conv_<conversation_id>.turn_<id>.external.<kind>.attachments/<event_id>/...
```

## Read vs Pull vs Checkout

```text
react.read
  Put readable logical content into context.
  Good for conv:ar, conv:tc, conv:so, conv:ws, conv:su, conv:ev, conv:fi.

react.pull
  Materialize bytes into this turn.
  Required before local search, code execution, patch, rendering, or file-byte inspection
  when the content is not already local in [WORKSPACE].

react.checkout
  Make historical project state editable.
  Only for conv:fi:conv_<conversation_id>.<turn>.git/projects/... refs.
```

## Search And Local Inspection

`react.rg` searches the local materialized workspace only. It does not search:

- unpulled `conv:fi:` historical refs;
- `mem:`, `task:`, `cnv:` owner refs;
- hidden/pruned timeline blocks;
- storage-provider URLs.

If a search needs older project state:

```text
1. Find the project ref from visible context or [WORKSPACE] REMOTE.
2. react.pull(paths=["conv:fi:conv_<conversation_id>.turn_<anchor>.git/projects/<project>"])
3. react.rg(paths=["turn_<anchor>/git/projects/<project>"], pattern="...")
```

If the older project must be edited:

```text
react.checkout(paths=["conv:fi:conv_<conversation_id>.turn_<anchor>.git/projects/<project>"], mode="replace")
```

Then edit:

```text
turn_<current>/git/projects/<project>/...
```

## Rendering And Exec

Rendering tools and exec code operate on physical files or inline content
according to their tool contracts. If the source is only available as a logical
ref, materialize it first.

Examples:

```text
source logical ref:
  conv:fi:conv_<conversation_id>.turn_1.files/report/source.html

pull result:
  physical_path = turn_1/files/report/source.html

render input:
  path/content/ref according to the rendering tool doc, using local source bytes
```

For project source files, use `git/projects`. For generated reports/downloads,
use `files`.

## Owner Projections

When `react.read` reads a materialized owner object, the stored metadata keeps
the owner identity:

```text
source_ref / object_ref = mem:mem_123
logical_path            = conv:fi:conv_<conversation_id>.turn_1.files/memory/mem_123.json
```

The owner-specific block-production policy can use `object_ref` to render a
better model-facing block than a raw JSON/text file. This does not make `mem:`
directly readable by `react.read`; the materialized `conv:fi:` row is still the
local conversation-owned artifact.

## Checklist

- Use `react.read` for readable conversation-owned records.
- Use `react.pull` when bytes must become local this turn.
- Use `react.checkout` only for editable `git/projects` project state.
- Use returned pull paths; do not invent materialized paths.
- Use `git/projects` for project state.
- Use `files` for produced artifacts.
- Use `git/snapshots` for state snapshots.
- Keep external owner refs (`mem:`, `task:`, `cnv:`) opaque until the owner
  rehoster returns materialized paths.
