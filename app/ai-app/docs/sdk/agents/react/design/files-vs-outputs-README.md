---
id: ks:docs/sdk/agents/react/design/files-vs-outputs-README.md
title: "Files vs Outputs"
summary: "Draft design for separating durable workspace files from non-workspace produced artifacts in React."
tags: ["sdk", "agents", "react", "design", "workspace", "artifacts"]
keywords: ["files namespace", "outputs namespace", "workspace membership", "external internal", "git workspace"]
see_also:
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/artifact-storage-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/design/git-based-isolated-workspace-README.md
status: draft
---
# Files vs Outputs

This is a draft design note.

Phase 1 of this split is now implemented.

It defines the split between:
- durable workspace/project state
- produced artifacts that should be kept or shared, but should not become part of workspace history

## Problem

Today React mostly writes assistant-produced files under:

```text
turn_<id>/files/...
```

That path currently mixes two different meanings:
- file is part of the durable project/workspace tree
- file is merely an output artifact for this turn

This becomes incorrect for cases such as:
- test results
- temporary reports
- generated analysis snapshots
- one-off exports for the user

Those artifacts may need to be:
- shown to the user
- hosted and downloadable
- visible to the agent later

but they should not automatically become:
- workspace members
- git-tracked project files
- part of the rolling workspace tree in `custom` mode

The existing `visibility=external|internal` axis does not solve this problem.

`visibility` only answers:
- should the user receive this artifact?

It does **not** answer:
- is this artifact part of the durable workspace/project tree?

## Proposed solution

Introduce a second assistant artifact namespace:

- `files/...`
  - durable workspace/project tree
  - eligible for workspace history
  - eligible for git publish in `git` mode
  - participates in workspace map / mental model

- `outputs/...`
  - produced artifact namespace
  - not part of workspace history
  - never committed to git
  - not part of the workspace tree shown to the agent as project state
  - still available for hosting, download, reuse, and later reading

Physical paths:

```text
turn_<id>/files/<relpath>
turn_<id>/outputs/<relpath>
turn_<id>/attachments/<name>
```

Logical paths:

```text
fi:<turn_id>.files/<relpath>
fi:<turn_id>.outputs/<relpath>
fi:<turn_id>.user.attachments/<name>
```

## Orthogonal visibility axis

Keep the current visibility axis unchanged:

- `visibility=external`
  - user receives it
- `visibility=internal`
  - agent/runtime only

This remains orthogonal to namespace.

That gives a clean matrix:

- `files/...` + `external`
  - project/workspace member that is also shared
- `files/...` + `internal`
  - project/workspace member not shared
- `outputs/...` + `external`
  - downloadable or visible artifact, not part of workspace history
- `outputs/...` + `internal`
  - internal artifact, not part of workspace history

## Agent mental model

React should be taught:

- `turn_<current_turn>/files/...` is the current project/workspace tree
- `turn_<current_turn>/outputs/...` is an artifact area, not the project tree
- if it wants durable project state, write to `files/...`
- if it wants a result/report/export/test-output that should not become project state, write to `outputs/...`

Examples:

- `files/bookbot/src/app.py`
  - workspace member
- `files/bookbot/README.md`
  - workspace member
- `outputs/bookbot/test_results.txt`
  - produced result, not workspace
- `outputs/bookbot/coverage.json`
  - produced diagnostic, not workspace
- `outputs/bookbot/report.md`
  - downloadable artifact, not workspace

## Effects by workspace backend

### `git`

- only `files/...` is eligible for staging/commit/publish
- `outputs/...` is ignored by workspace publish
- `react.pull(fi:<turn>.files/...)`
  - remains the historical workspace activation mechanism
- `react.pull(fi:<turn>.outputs/...)`
  - should be supported as explicit artifact retrieval
  - phase 1 can be exact-file only

### `custom`

- rolling workspace map tracks only `files/...`
- `outputs/...` does not become part of the workspace map
- `react.pull(fi:<turn>.outputs/...)`
  - is still allowed as artifact retrieval

## Turn fetch / UI impact

This split should not require a new UI artifact family immediately.

Current fetch already surfaces:
- `artifact:assistant.file` for external files

That can remain true for both:
- external `files/...`
- external `outputs/...`

The difference is in workspace semantics, not necessarily in basic user download behavior.

If needed later, fetch can expose namespace/kind more explicitly in metadata.

## Discovery / storage implications

Artifact discovery must understand:

- `fi:<turn_id>.files/...`
- `fi:<turn_id>.outputs/...`
- `fi:<turn_id>.user.attachments/...`

Artifact storage must preserve:

- namespace
- visibility
- kind

but workspace publish / workspace maps must only consider:

- `files/...`

## Phase 1 rollout

Recommended rollout:

1. Add `outputs/...` path normalization and logical mapping
2. Keep `files/...` behavior unchanged
3. Keep `visibility=external|internal` unchanged
4. Make git publish stage only `files/...`
5. Teach React:
   - `files/...` = project tree
   - `outputs/...` = produced artifacts
6. Add exact `react.pull(fi:<turn>.outputs/<file>)`
7. Later decide whether folder pulls for `outputs/...` are needed

## Acceptance criteria

- React has a clear path-level distinction between workspace state and produced artifacts
- `visibility` remains separate from workspace membership
- git publish never stages `outputs/...`
- custom workspace maps never treat `outputs/...` as workspace members
- external outputs remain downloadable through the normal artifact flow
- internal outputs remain agent-visible without being user-emitted

## Non-goals

- no attempt here to redesign user attachment paths
- no automatic inference from file type or filename
- no `.gitignore`-based implicit contract for workspace membership
- no promise yet that `outputs/...` folder pulls will ever be supported; exact-file pull is the phase-1 contract
