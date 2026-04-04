---
id: ks:docs/sdk/agents/react/design/custom-isolated-workspace-mental-map-README.md
title: "Draft: Custom Isolated Workspace Mental Map"
summary: "Draft design for a rolling workspace map in React custom workspace mode, showing latest known file versions and deletions without git backing."
draft: true
status: draft
tags: ["sdk", "agents", "react", "design", "workspace", "custom", "timeline"]
keywords: ["custom workspace", "mental map", "workspace tree", "file versions", "deleted files", "announce"]
see_also:
  - ks:docs/sdk/agents/react/design/git-based-isolated-workspace-README.md
  - ks:docs/sdk/agents/react/design/workspace-checkout-model-README.md
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
---

# Custom Isolated Workspace Mental Map

This is a draft design for the `workspace_implementation=custom` path.

The goal is to give React a stable **mental model of the workspace tree** even
when the backend is not git-backed. The model should behave like a rolling
workspace index:

- one latest known version per file path
- deleted files remain represented as deleted
- the map is cheap to maintain incrementally
- React can use it to understand what workspace paths exist before deciding
  whether to call `react.pull(...)`, `react.read(...)`, `react.patch(...)`, or
  exec

---

## 1) JIRA Ticket

### Summary

Add a rolling workspace mental map for `custom` workspace mode so React can see
the current known workspace tree, the last version of each file, and deletions,
without scanning the full conversation history on each turn.

### Problem

In `custom` workspace mode, React works with:

- `fi:<turn_id>.files/...`
- `fi:<turn_id>.user.attachments/...`
- explicit `react.pull(paths=[...])`

That gives correct addressing semantics, but it does **not** give React a compact
answer to:

- what files are part of the current workspace picture
- which version is the latest known version for each file
- whether a file was deleted later
- which scope names are meaningful to refer to

Without that rolling picture, React has to infer too much from raw turn history
or from whatever happens to be currently materialized locally.

There is also an important semantic gap today:

- the `custom` backend does not yet have a first-class delete operation
- a file can be absent from the current turn simply because React did not copy
  it forward
- that is not the same thing as an explicit delete

So the system cannot yet safely distinguish:

- intentional workspace delete
- not materialized in this turn

That means tombstones in the rolling map cannot be fully trustworthy until
delete intent becomes an explicit runtime-observed operation.

### Proposed Solution

Maintain a conversation-level **workspace mental map** in `custom` mode:

- keyed by logical workspace path under `files/`
- value = latest known version entry
- deleted files remain as explicit tombstones

The map is persisted similarly to the rolling `sources_pool` idea:

- one current compact map
- incrementally updated from new turn artifacts
- surfaced briefly in ANNOUNCE
- recoverable in fuller form through a stable artifact path

Important prerequisite:

- the `custom` backend needs an explicit delete-capable operation so the map
  can record deletion as an intentional state change rather than guessing from
  file absence

### Acceptance Criteria

- React in `custom` mode can see a compact workspace map in ANNOUNCE
- each file path shows its latest known `turn_id`
- deleted files stay visible as deleted
- delete tombstones are created only from explicit delete operations
- the map is updated incrementally, not by full historical rescans on every turn
- React can use the map to decide what to pull or inspect

### Non-Goals

- replacing `fi:` refs
- introducing git semantics into `custom` mode
- automatic full workspace hydration
- implicit binary folder membership rules
- guessing deletes from missing files

---

## 2) Core Idea

The custom backend should maintain a rolling structure like:

```json
{
  "scopes": {
    "projectA": {
      "src/app.py": {
        "latest_turn_id": "turn_1775...",
        "status": "present",
        "kind": "text"
      },
      "assets/logo.png": {
        "latest_turn_id": "turn_1774...",
        "status": "present",
        "kind": "binary"
      },
      "docs/old.md": {
        "latest_turn_id": "turn_1773...",
        "status": "deleted"
      }
    }
  }
}
```

Important property:

- for each logical path, store only the **latest known state**
- but keep deletion state instead of dropping the path entirely

Important caveat:

- deletion state is reliable only after `custom` mode gets a first-class delete
  operation
- until then, the map can still track latest known files, but deleted state is
  only a design target, not something to infer from absence

That makes the map useful for React reasoning:

- "this file exists and latest version is from turn X"
- "this file was deleted later"
- "this scope has these known paths"

The missing operational companion to this map is defined in
`design/workspace-checkout-model-README.md`:

- the mental map answers **what scopes and latest versions exist**
- checkout should answer **what gets materialized into `turn_<current>/files/`**

Those two pieces should work together in `custom` mode:

- ANNOUNCE shows the known scopes from the rolling map
- React chooses which scope/version refs to check out into the current turn
- `react.pull(...)` remains historical side materialization, not current-workspace population

---

## 3) ANNOUNCE Presentation

The map shown in ANNOUNCE should stay brief.

Example:

```text
[WORKSPACE]
  implementation: custom
  known_scopes:
    - projectA/ (12 files, 1 deleted)
    - projectB/ (3 files)
  latest_known_updates:
    - projectA/src/app.py -> turn_1775...
    - projectA/docs/old.md -> deleted @ turn_1773...
```

ANNOUNCE is **not** the place to dump the whole tree.

It should show:

- implementation
- known scopes
- a few latest/most relevant path states
- enough signal so React knows what exists and what to pull

The fuller map should live as a normal artifact / internal state object.

---

## 4) Update Model

The custom workspace map should be updated incrementally from turn outputs:

- `react.write`
- `react.patch`
- exec-produced file artifacts
- explicit deletion notices
- hosted artifact records

Expected update operations:

- create or replace file node
- mark file node deleted
- refresh scope counts / summaries

Delete rule:

- `mark file node deleted` must be driven by an explicit delete-capable runtime
  operation
- it must not be inferred only because a file is missing from the current turn

This should happen from new turn contributions only.

Avoid:

- scanning the entire conversation on every turn

Allow:

- full rebuild as a repair tool if corruption is suspected

---

## 5) Binary Files

The custom workspace map must still track binary paths as members of the
workspace picture:

- `.xlsx`
- `.pptx`
- `.docx`
- images
- PDFs

But the map should **not** imply that folder-level pulls hydrate those binaries
automatically.

So:

- map tracks binary logical paths
- ANNOUNCE can mention them
- React still must pull binaries point-wise by exact `fi:` file ref

---

## 6) Integration Points

Likely implementation points:

- `react/v2/timeline.py`
  - durable storage of the rolling map
- `react/v2/layout.py`
  - compact ANNOUNCE section
- `react/v2/runtime.py`
  - refresh/update hook per turn
- `react/v2/solution_workspace.py`
  - custom-mode hydration already uses artifact/history state and should become a producer for the map
- `react/v2/tools/write.py`
- `react/v2/tools/patch.py`
- `react/v2/tools/...` delete-capable custom workspace operation
- `react/v2/tools/external.py`
  - these are major sources of file creation/update/delete signals

---

## 7) Why This Matters

This is the custom-backend analog of the git workspace mental model.

`git` mode gives React:

- local repo history
- diff/status/log semantics

`custom` mode cannot rely on git, so it needs a **rolling declarative map**
instead.

That map should let React reason about:

- what workspace paths exist
- which version is the latest one
- what was deleted
- what needs explicit pull before code/execution

---

## 8) Recommended Next Slice

1. Add a first-class delete operation for `custom` workspace mode
2. Define the internal payload schema for the rolling custom workspace map
3. Update the map from new file-producing/deleting turn events
4. Surface a compact summary under `[WORKSPACE]` in ANNOUNCE when
   `workspace_implementation=custom`
5. Add a stable internal artifact path for the latest full map
