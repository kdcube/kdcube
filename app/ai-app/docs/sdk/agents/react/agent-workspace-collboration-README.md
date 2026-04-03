---
id: ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
title: "Agent Workspace Collaboration"
summary: "How react.read, react.write, react.patch, and react.search_files cooperate across current-turn OUT_DIR, conversation artifacts, and read-only bundle knowledge space."
tags: ["sdk", "agents", "react", "workspace", "artifacts", "files"]
keywords: ["outdir", "workdir", "react.read", "react.search_files", "react.patch", "versioned workspace"]
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
1) CURRENT TURN OUT_DIR (physical)
   out/turn_<id>/files/...
   out/turn_<id>/outputs/...   # produced artifacts, not workspace members
   out/turn_<id>/attachments/...
   out/logs/...

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
- for non-versioned OUT_DIR folders like `logs/`: the physical OUT_DIR file itself
- for `ks:`: a read-only logical namespace owned by the bundle, not a directory under OUT_DIR

## Read / search / write responsibilities

`react.read`
- reads by logical path only
- supports the established turn-scoped forms:
  - `fi:<turn_id>.files/<subpath>`
  - `fi:<turn_id>.outputs/<subpath>`
  - `fi:<turn_id>.user.attachments/<subpath>`
- also supports any readable OUT_DIR file via:
  - `fi:<outdir-relative-path>`
  - example: `fi:logs/docker.err.log`
- also supports exact `ks:` paths:
  - `ks:<relpath>`
  - example: `ks:<bundle-defined-path>`

`react.search_files`
- searches under `outdir` or `workdir`
- returns discovery metadata only
- does not browse `ks:` or conversation artifact memory
- result shape:
  - `root`
  - `hits[]`
- each hit contains:
  - `path`: relative to the searched root
  - `size_bytes`
  - `logical_path` for OUT_DIR hits

`react.write`
- creates or replaces files in the current turn namespaces
- `files/...` means durable workspace/project state
- `outputs/...` means artifacts that should be kept/shared but should not become durable workspace state

`react.patch`
- updates an existing file in the current turn file namespace
- historical `turn_X/files/...` references are treated as source material and patched into the current turn output

## Knowledge space browsing

`ks:` is not part of the current-turn OUT_DIR tree.

Current rules:
- if the exact `ks:` path is known, `react.read` can load it directly
- `react.search_files` does not browse `ks:`
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
1. Use `react.search_files` to discover candidate files.
2. If the hit is under OUT_DIR, take its `logical_path`.
3. Use `react.read` on that `logical_path` to load content into context.
4. If editing is needed, write or patch into `<current_turn>/files/...`.
5. If producing a report/export/result that should not become workspace state, write it into `<current_turn>/outputs/...`.

This keeps:
- discovery separate from loading
- loading separate from mutation
- turn history preserved

## Current limitations

- `react.read` is OUT_DIR-aware, not general workdir-aware.
- workdir hits from `react.search_files` are still discovery-only with the current toolset.
- `react.patch` is for assistant file artifacts, not arbitrary runtime-owned files like `logs/...`.

## Examples

Search logs and read one:
```json
{
  "root": "outdir/logs",
  "hits": [
    {
      "path": "docker.err.log",
      "size_bytes": 18342,
      "logical_path": "fi:logs/docker.err.log"
    }
  ]
}
```

Search turn files and read one:
```json
{
  "root": "outdir",
  "hits": [
    {
      "path": "turn_1773261747483_vfm2tt/files/kdcube-market-comparison.md",
      "size_bytes": 9123,
      "logical_path": "fi:turn_1773261747483_vfm2tt.files/kdcube-market-comparison.md"
    }
  ]
}
```
