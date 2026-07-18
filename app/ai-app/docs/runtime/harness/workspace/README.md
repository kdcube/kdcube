---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
title: "Agent Harness Workspace"
summary: "Framework-neutral refs, artifacts, paths, change detection, and materialization for distributed turn workspaces."
tags: ["runtime", "harness", "workspace", "artifacts", "refs", "materialization"]
updated_at: 2026-07-18
keywords:
  [
    "turn workspace",
    "conv:fi",
    "git/projects",
    "files",
    "git/snapshots",
    "OUTPUT_DIR",
    "pull",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/artifact-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-lifecycle-and-distribution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/artifact-resolution-and-materialization-README.md
---
# Agent Harness Workspace

The harness workspace is a sparse, per-turn physical view over durable
conversation and owner-domain artifacts.

```text
logical refs and owner objects
             |
       trusted resolution
             |
             v
OUTPUT_DIR/
  turn_<current>/
    git/projects/...
    files/...
    git/snapshots/...
    attachments/...
    external/...
```

The shared workspace layer owns:

- canonical `conv:fi:` refs and `conv_<conversation_id>` ownership segments;
- physical/logical path construction and parsing;
- the semantic distinction between project state, produced files, snapshots,
  attachments, and external evidence;
- artifact records independent of timeline placement;
- artifact-root resolution, snapshots, diffs, and file-item production;
- trusted byte materialization into a caller-supplied destination.

It does not own ReAct tool names. `react.pull`, `react.checkout`, `react.read`,
`react.rg`, `react.write`, and `react.patch` are ReAct adapter surfaces over
parts of this contract. The ported LangGraph example consumes the common pull
and path primitives without pretending to be ReAct.

## Area Semantics

| Area | Meaning |
| --- | --- |
| `git/projects/` | Editable durable project/app state. |
| `files/` | Produced artifacts and deliverables. |
| `git/snapshots/` | Story, canvas, wizard, or workflow snapshots. |
| `attachments/` | Current-turn user uploads. |
| `external/` | Materialized external-event or owner-domain evidence. |

Visibility is separate from area. An internal file remains a file; a timeline
message does not become an artifact merely because it is visible.

## Canonical Documents

- [References And Paths](references-and-paths-README.md)
- [Workspace Model](workspace-model-README.md)
- [Artifact Storage](artifact-storage-README.md)
- [Workspace Lifecycle And Distribution](workspace-lifecycle-and-distribution-README.md)
- [Artifact Resolution And Materialization](../events/artifact-resolution-and-materialization-README.md)
