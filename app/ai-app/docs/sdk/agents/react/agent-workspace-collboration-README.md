---
id: ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
title: "Agent Workspace Collaboration"
summary: "How react.read, react.write, react.patch, and react.rg cooperate across the current-turn artifact root, conversation artifacts, and read-only bundle knowledge space."
tags: ["sdk", "agents", "react", "workspace", "artifacts", "files"]
keywords: ["outdir", "react.read", "react.rg", "react.patch", "versioned workspace"]
see_also:
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
  - ks:docs/sdk/agents/react/design/files-vs-outputs-README.md
---
# Agent Workspace Collaboration

This document explains the working model the React agent should use when combining the filesystem tools.
It is about the **current** agent model, not the future shared mutable workspace design.

Scope:
- this doc is about the agent mental model and tool cooperation
- the actual filesystem lifecycle is covered by `react-turn-workspace-README.md`
- the current `files/...` vs `outputs/...` split is tracked in `design/files-vs-outputs-README.md`

## Core model

The agent does **not** work against one mutable flat directory. It reasons across these surfaces:

```text
1) CURRENT TURN ARTIFACT ROOT (physical)
   OUTPUT_DIR == artifact root
   local host layout: out/workdir/
     turn_<id>/files/...
     turn_<id>/outputs/...     # produced artifacts, not workspace members
     turn_<id>/attachments/...

   Runtime metadata root, not an agent artifact namespace:
   out/
     timeline.json
     tool_calls_index.json
     logs/...

2) CONVERSATION ARTIFACT MEMORY (logical)
   ar:...  tc:...  so:...  su:...
   fi:<older_turn>.files/...
   fi:<older_turn>.user.attachments/...

3) BUNDLE KNOWLEDGE SPACE (logical, read-only)
   ks:<bundle-defined-path>/...
```

This gives two properties at once:
- history is preserved in place
- the agent can still reason about a current logical workspace view

The logical workspace view is:
- for `files/<subpath>`: the latest relevant turn version
- for `outputs/<subpath>`: a produced artifact area, not part of the workspace tree
- for `attachments/<subpath>`: the attached artifact under its original turn namespace
- runtime folders like `logs/`: platform diagnostics, not normal agent artifact paths
- for `ks:`: a read-only logical namespace owned by the bundle, not a directory under the artifact root

The agent should only use visible `turn_...` relative paths or logical paths
(`fi:`, `ar:`, `tc:`, `so:`, `su:`, `ks:`). It should not use absolute host
paths, execution sandbox paths, hosted `file://` paths, or the runtime
metadata root.

## Read / search / write responsibilities

`react.read`
- reads by logical path only
- supports the established turn-scoped forms:
  - `fi:<turn_id>.files/<subpath>`
  - `fi:<turn_id>.outputs/<subpath>`
  - `fi:<turn_id>.user.attachments/<subpath>`
- also supports any readable artifact-root file via:
  - `fi:<artifact-root-relative-path>`
  - example: `fi:turn_<id>/outputs/report.md`
- also supports exact `ks:` paths:
  - `ks:<relpath>`
  - example: `ks:<bundle-defined-path>`

`react.rg`
- searches filenames and/or text for files already materialized in the local artifact workspace
- accepts visible path roots such as `files/...`, `outputs/...`, `attachments/...`, `turn_<id>/files/...`, `turn_<id>/outputs/...`, `turn_<id>/attachments/...`, or matching `fi:` artifact paths
- returns discovery metadata and line-oriented match ranges
- does not browse `ks:`, hidden/pruned timeline, unpulled snapshots, or conversation artifact memory
- result shape:
  - `root`
  - `hits[]`
- each hit contains:
  - `path`: relative to the searched root
  - `size_bytes`
  - `text_symbols` and `line_count` for text files when available
  - `logical_path` for readable hits
  - `matches[]` for text searches, each with a line number and a ready-to-pass `read_item`

`react.write`
- creates or replaces files in the current turn namespaces
- `files/...` means durable workspace/project state
- `outputs/...` means artifacts that should be kept/shared but should not become durable workspace state
- unqualified paths default to `outputs/...`; use `files/...` explicitly for durable workspace/project state

`react.patch`
- updates an existing current-turn materialized text file under `files/...` or `outputs/...`
- does not require the file to have been created by `react.write`; current-turn files produced by exec are patchable once present locally
- historical `turn_X/files/...` references are source material. Pull them first if needed; if editing is intended, checkout/copy them into the current turn and patch the current-turn file.

## Knowledge space browsing

`ks:` is not part of the current-turn artifact tree.

Current rules:
- if the exact `ks:` path is known, `react.read` can load it directly
- `react.rg` does not browse `ks:`
- directory-style `ks:` browsing is only possible inside isolated exec if the bundle exposes a namespace resolver/helper

When exec-time `ks:` browsing exists:
1. code starts from a logical ref such as `ks:<bundle-defined-root>`
2. the bundle resolver returns an exec-local physical path
3. code inspects descendants under that path
4. code emits logical refs such as `ks:<bundle-defined-root>/foo/bar.py`
5. the agent later uses `react.read` on those logical refs

If no resolver exists, `ks:` is still readable by exact path, but not browseable as a directory tree from normal React tools.

## Safe collaboration rules

The intended cooperation pattern is:
1. If the needed file is from older conversation state and is not local yet, identify its `fi:` ref from visible context or `react.memsearch`, then `react.pull` it.
2. Use `react.rg` to discover candidate local files or exact text regions.
3. Take the returned `logical_path`, or the returned `read_item` for exact ranges.
4. Use `react.read` on that `logical_path`, or pass `read_item` ranges as `items`, to load the needed content into context.
5. If editing older state is needed, checkout the pulled `fi:<turn>.files/...` ref into `<current_turn>/files/...`, then write or patch the current-turn copy.
6. If producing a report/export/result that should not become workspace state, write it into `<current_turn>/outputs/...`.

This keeps:
- discovery separate from loading
- loading separate from mutation
- turn history preserved

## Current limitations

- `react.rg` searches readable, already materialized artifact files, not internal execution scratch or the whole conversation timeline.
- `react.patch` is for existing current-turn materialized text files. Prefer current-turn `files/<scope>/...` for durable project edits and `outputs/<scope>/...` for edited artifacts that should not enter workspace history.

## Examples

Search current-turn output files and read one:
```json
{
  "root": "outputs/logs",
  "hits": [
    {
      "path": "docker.err.log",
      "size_bytes": 18342,
      "logical_path": "fi:<current_turn>.outputs/logs/docker.err.log"
    }
  ]
}
```

Search turn files and read one:
```json
{
  "root": "turn_1773261747483_vfm2tt/files",
  "hits": [
    {
      "path": "kdcube-market-comparison.md",
      "size_bytes": 9123,
      "logical_path": "fi:turn_1773261747483_vfm2tt.files/kdcube-market-comparison.md"
    }
  ]
}
```
