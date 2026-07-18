---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
title: "Agent Harness Workspace Model"
summary: "Authoritative contract for the sparse per-turn workspace, project state, produced files, snapshots, attachments, and materialization."
status: active
tags: ["runtime", "harness", "workspace", "pull", "checkout", "artifacts"]
updated_at: 2026-07-18
keywords:
  [
    "sparse workspace",
    "conv:fi",
    "git/projects",
    "files",
    "git/snapshots",
    "materialization",
    "bound user scope",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-lifecycle-and-distribution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/artifact-resolution-and-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
---
# Agent Harness Workspace Model

The harness workspace is sparse:

```text
each turn receives a fresh physical workspace
durable logical refs survive across turns and workers
bytes appear locally only after ingress, production, pull, or checkout
```

This model is shared. ReAct's `[WORKSPACE]` ANNOUNCE and model tools are one
adapter over it; ported agents can consume the same path and materialization
contracts through their own adapter.

## Physical Layout

```text
OUTPUT_DIR/
  turn_<current>/
    git/projects/<project_scope>/...      # editable durable project state
    files/<artifact_scope>/...            # produced artifacts/deliverables
    git/snapshots/<snapshot_scope>/...     # story/workflow snapshots
    attachments/<name>                    # current user uploads
    external/<kind>/attachments/<id>/...  # external/domain evidence
  conv_<source_conversation>/
    turn_<source_turn>/...                 # pulled cross-conversation material
```

Only `OUTPUT_DIR`-relative paths are part of the harness contract. Host paths,
container mount prefixes, object-store URIs, and runtime metadata roots are not
agent file paths.

## Logical Refs

The durable identity for a file includes the conversation owner:

```text
conv:fi:conv_<conversation_id>.turn_<turn_id>.git/projects/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.files/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.git/snapshots/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.user.attachments/<name>
conv:fi:conv_<conversation_id>.turn_<turn_id>.external.<kind>.attachments/<id>/<name>
```

See [References And Paths](references-and-paths-README.md) for the full
conversation ref grammar and authority boundary.

## Area Is Meaning

Do not infer artifact meaning from extension or visibility.

| Area | Put here | Do not put here |
| --- | --- | --- |
| `git/projects/` | Source trees, app state, editable durable project files. | One-off reports and downloads. |
| `files/` | PDF/DOCX/PPTX/XLSX/HTML/Markdown reports, archives, diagnostics, render sources, exported deliverables. | Durable project source trees. |
| `git/snapshots/` | Canvas/story/wizard/workflow state snapshots. | User deliverables unless explicitly exported. |
| `attachments/` | User-uploaded bytes. | Assistant-produced output. |
| `external/` | Event attachments and owner-domain evidence. | Direct editable project state. |

Visibility is orthogonal:

```text
git/projects + external  -> visible project artifact
git/projects + internal  -> hidden project state
files        + external  -> user/client deliverable
files        + internal  -> runtime/agent artifact
```

## Materialization

```text
logical ref or owner ref
          |
          v
trusted resolver under runtime identity
          |
          v
bytes copied into a selected workspace destination
          |
          v
adapter returns logical_path + physical_path
```

The runtime, not the model, binds tenant, project, actor, user, and authority.
For owner refs such as `mem:`, `task:`, or `cnv:`, the owner provider/rehoster
authorizes and chooses the resulting artifact semantics.

The framework-neutral primitive is
`runtime.harness.workspace.pull_refs_into_dir(...)`. It accepts canonical
conversation refs and writes resolved bytes into a supplied destination. The
ReAct adapter adds richer `react.pull` behavior, including owner namespace
rehosters and returned workspace rows.

## ReAct Adapter

ReAct maps model-facing operations onto the shared workspace:

| ReAct tool | Adapter behavior |
| --- | --- |
| `react.pull` | Materializes historical `conv:fi:` refs or registered owner refs and returns actual paths. |
| `react.checkout` | Copies historical `git/projects` state into the current editable tree. |
| `react.read` | Loads supported logical records or file content into model context. |
| `react.rg` | Searches only materialized local files. |
| `react.write` / `react.patch` | Mutates current-turn project, file, or snapshot paths according to tool policy. |

`react.checkout` accepts project state, not arbitrary files:

```text
valid:
  conv:fi:conv_<conversation_id>.turn_<turn_id>.git/projects/<scope>

invalid:
  mem:...
  task:...
  cnv:...
  conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report.pdf
  conv:fi:conv_<conversation_id>.turn_<turn_id>.git/snapshots/story.json
```

After checkout, edit:

```text
turn_<current>/git/projects/<scope>/...
```

not the historical source path.

## Other Agent Adapters

A ported agent is not required to expose ReAct tools. It can:

1. select refs from its own input/state;
2. call the shared resolver or pull primitive;
3. place bytes in its turn workspace;
4. execute against physical paths;
5. emit canonical `conv:fi:` refs for resulting artifacts.

The worked LangGraph port uses this pattern for user/event attachments and
code-exec outputs.

## ReAct `[WORKSPACE]`

ReAct rebuilds an ANNOUNCE view every round:

```text
LOCAL
  materialized bytes in this worker now

REMOTE git branch
  durable project scopes available from conversation lineage
  but not local until pulled or checked out
```

That presentation is ReAct-specific. The underlying sparse workspace and
logical refs are shared.

## Debug Checklist

1. Is the value a logical ref or an `OUTPUT_DIR`-relative physical path?
2. Does a durable ref include `conv_<conversation_id>`?
3. Are the bytes materialized in this worker?
4. Is the area correct for the object's meaning?
5. Did resolution run under the expected tenant/project/user/authority?
6. For an owner namespace, is its provider/rehoster registered and authorized?
7. Is an adapter confusing project checkout with ordinary byte pull?
