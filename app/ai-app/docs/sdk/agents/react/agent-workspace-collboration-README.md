---
id: ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
title: "Agent Workspace Collaboration"
summary: "Canonical ReAct workspace and artifact-origin contract: logical refs, physical OUT_DIR layout, files/outputs/snapshots/attachments semantics, cross-conversation refs, custom namespace rehosters, and read/search/write/pull/checkout cooperation."
tags: ["sdk", "agents", "react", "workspace", "artifacts", "files"]
keywords: ["outdir", "react.read", "react.rg", "react.patch", "versioned workspace"]
see_also:
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
  - ks:docs/sdk/agents/react/files-vs-outputs-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
---
# Agent Workspace Collaboration

This document explains the working model the React agent should use when combining the filesystem tools.
It is about the **current** agent model, not the future shared mutable workspace design.

Scope:
- this doc is the canonical agent-facing workspace/artifact contract
- the actual filesystem lifecycle is covered by `react-turn-workspace-README.md`
- custom-mode edge cases are covered by `custom-isolated-workspace-mental-map-README.md`
- git-mode engineering details are covered by `workspace/git-based-isolated-workspace-README.md`

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
   fi:<older_turn>.outputs/...
   fi:<older_turn>.snapshots/...
   fi:<older_turn>.user.attachments/...
   fi:<older_turn>.external.<event_kind>.attachments/<event_id>/...
   fi:conv_<conversation_id>.turn_<older>.files|outputs|snapshots/...

3) CUSTOM ARTIFACT NAMESPACE REFS (logical, opaque until pulled)
   nmsp:<domain-defined-key>
   <other_namespace>:<domain-defined-key>

4) BUNDLE KNOWLEDGE SPACE (logical, read-only)
   ks:<bundle-defined-path>/...
```

This gives two properties at once:
- history is preserved in place
- the agent can still reason about a current logical workspace view

The logical workspace view is:
- for `files/<subpath>`: the latest relevant turn version
- for `outputs/<subpath>`: a produced artifact area, not part of the workspace tree
- for `snapshots/<subpath>`: story/state snapshots, not ordinary project files
- for `attachments/<subpath>`: the attached artifact under its original turn namespace
- runtime folders like `logs/`: platform diagnostics, not normal agent artifact paths
- for `ks:`: a read-only logical namespace owned by the bundle, not a directory under the artifact root
- for custom namespace refs such as `nmsp:...`: a domain artifact handle that
  has no derived filesystem path until `react.pull` invokes a registered
  rehoster

The agent should only use visible `turn_...` relative paths or logical paths
(`fi:`, `ar:`, `tc:`, `so:`, `su:`, `ks:`, and registered owner namespace
refs such as `nmsp:`). It should not use absolute host paths, execution sandbox
paths, hosted `file://` paths, or the runtime metadata root.

## Namespace semantics

The namespace after the turn id defines artifact meaning. Do not infer meaning
from file extension alone.

```text
files/       durable workspace/project state
outputs/     produced artifacts, reports, render sources, diagnostics
snapshots/   story/workflow state snapshots
attachments/ user-uploaded files for a turn
external/    externally authored/domain/followup attachments rehosted for ReAct
```

`files/` is the only namespace that represents current editable project state.
It is the namespace that `react.checkout` populates under
`turn_<current>/files/...`.

`outputs/` is not workspace history. It can hold HTML, Markdown, JSON, images,
PDFs, logs, reports, and render sources, but those are produced artifacts, not
the project tree.

`snapshots/` is separate from `files/` even when the snapshot is text. A
snapshot records current story/workflow state, for example a wizard state,
canvas state, or user-story state. It should not be treated as project source
unless the bundle deliberately rehosts or writes it into `files/`.

Visibility is orthogonal:

```text
files/...   + external  -> workspace member also emitted to the user
files/...   + internal  -> workspace member not emitted to the user
outputs/... + external  -> downloadable/visible artifact, not workspace state
outputs/... + internal  -> runtime/agent artifact, not workspace state
```

## Artifact origin to workspace layout

The agent should separate **where an artifact came from** from **where
`react.pull` materialized it locally**.

```text
Current turn artifact, already local:
  logical:  fi:turn_<current>.files/app/src/main.py
  physical: turn_<current>/files/app/src/main.py

Same conversation, older turn:
  source:   fi:turn_111.outputs/report.html
  pull ->   turn_111/outputs/report.html

Other conversation:
  source:   fi:conv_conversation-42.turn_222.snapshots/wizard/current.yaml
  pull ->   conv_conversation-42/turn_222/snapshots/wizard/current.yaml

Custom namespace artifact:
  source:   nmsp:draft_browser-crash/issue-draft.yaml
  pull ->   fi:turn_<current>.snapshots/nmsp/draft_browser-crash/issue-draft.yaml
            turn_<current>/snapshots/nmsp/draft_browser-crash/issue-draft.yaml
```

After pulling a mixed set of refs, the local `OUTPUT_DIR` artifact root can look
like this:

```text
OUTPUT_DIR/
  turn_<current>/
    files/
      app/                         # editable current project/workspace state
        src/main.py
    outputs/
      app/
        test-results.txt           # produced artifact, not workspace state
    snapshots/
      ext/
        task-tracker/
          draft_browser-crash/
            issue-draft.yaml       # rehosted custom namespace snapshot
    external/
      ext/
        attachments/
          ext_ab12cd34/
            ext/
              task-tracker/
                draft_browser-crash/
                  evidence/
                    att_123__screenshot.png

  turn_111/
    files/
      app/src/main.py              # pulled older same-conversation file
    outputs/
      report.html                  # pulled older same-conversation output
    snapshots/
      story.yaml                   # pulled older same-conversation snapshot

  conv_conversation-42/
    turn_222/
      files/
        app/src/other.py           # pulled cross-conversation file
      outputs/
        other-report.html          # pulled cross-conversation output
      snapshots/
        wizard/current.yaml        # pulled cross-conversation snapshot
```

Rules:

- `fi:turn_...` belongs to the current conversation.
- `fi:conv_<conversation_id>.turn_...` belongs to another conversation; the
  matching local physical root is `conv_<conversation_id>/turn_...`.
- `nmsp:` is only an example owner-domain namespace. It is not a universal
  built-in artifact store. A bundle or SDK module must register a rehoster for
  the namespace before `react.pull(paths=["nmsp:..."])` can materialize it.
- The agent must not derive `fi:` paths from custom namespace refs. It calls
  `react.pull` and uses the returned `logical_path` / `physical_path` rows.

## Pull vs checkout

`react.pull` and `react.checkout` intentionally do different jobs.

`react.pull` materializes source refs locally:

```text
fi:turn_111.files/app/src/main.py
  -> turn_111/files/app/src/main.py

fi:conv_conversation-42.turn_222.snapshots/wizard/current.yaml
  -> conv_conversation-42/turn_222/snapshots/wizard/current.yaml

nmsp:draft_1/issue-draft.yaml
  -> returns source_ref + materialized logical_path/physical_path chosen by the rehoster
```

Pulling does **not** make the active editable workspace change. Pulled content
is reference material unless the agent explicitly copies/checks it into the
current turn.

`react.checkout` defines the current editable workspace:

```json
{
  "paths": ["fi:turn_111.files/app"],
  "mode": "replace"
}
```

Result:

```text
turn_<current>/files/app/...
```

Checkout rules:

- accepts `fi:...files...` refs only;
- does not accept owner-domain namespace refs such as `nmsp:` directly;
- `mode="replace"` clears `turn_<current>/files/` and applies the requested
  refs in order;
- `mode="overlay"` keeps the existing current `files/` tree and applies the
  requested refs on top;
- after checkout, patch/write/run/search the current copy under
  `turn_<current>/files/...`, not the historical `turn_<older>/...` copy.

If a custom artifact must become editable project state, first pull it, then
explicitly write/copy the intended current-turn project file under
`turn_<current>/files/...`.

## Custom namespace rehosters

Custom namespace refs are useful when timeline events or snapshots point at
domain-owned artifacts that are not yet ReAct artifacts. A namespace rehoster
bridges that domain ref into the ReAct artifact model.

The rehoster must know the ReAct workspace layout. Its job is to choose the
destination surface by artifact meaning, write/copy bytes under the matching
`OUTPUT_DIR` physical path, and return the `fi:` logical path plus physical
path that the agent should use after `react.pull`. The structure is defined in
[ReAct Turn Workspace](./react-turn-workspace-README.md) and the namespace
semantics are summarized in [Files vs Outputs](./files-vs-outputs-README.md).

Register the rehoster in a tool/event module loaded into the ReAct runtime:

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_external_attachment_logical_path,
    build_external_attachment_physical_path,
    build_logical_artifact_path,
    build_physical_artifact_path,
)

@artifact_namespace_rehoster(namespace="nmsp")
async def rehost_nmsp_ref(*, ref, key, ctx_browser, outdir, **_):
    turn_id = ctx_browser.runtime_ctx.turn_id

    # Choose the destination by artifact meaning, not by filename alone.
    physical_path = build_physical_artifact_path(
        turn_id=turn_id,
        namespace="snapshots",
        relpath=f"nmsp/{key}",
    )
    logical_path = build_logical_artifact_path(
        turn_id=turn_id,
        namespace="snapshots",
        relpath=f"nmsp/{key}",
    )

    target = resolve_artifact_path(outdir, physical_path, prefer_existing=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(read_domain_artifact_bytes(key))

    return {
        "materialized": [{
            "source_ref": ref,
            "logical_path": logical_path,
            "physical_path": physical_path,
            "file_count": 1,
        }]
    }
```

For the function to be discoverable, the module must be part of the
`EventSourceSubsystem` input. Use one of these bundle patterns:

```text
Pattern A: rehoster lives in a tool module

tools_descriptor.py
  TOOLS_SPECS = [
    {"ref": "tools/my_tools.py", "alias": "my_tools"},
  ]

tools/my_tools.py
  @artifact_namespace_rehoster(namespace="nmsp")
  async def rehost_nmsp_ref(...): ...

ToolSubsystem loads tools/my_tools.py, then EventSourceSubsystem scans that
loaded tool module for event sources and namespace rehosters.
```

```text
Pattern B: rehoster lives in an event-only module

events_descriptor.py
  EVENT_SOURCE_SPECS = [
    {"ref": "events/my_artifacts.py", "alias": "my_artifacts"},
  ]

orchestrator/workflow.py
  from .. import events_descriptor, tools_descriptor

  react = self.build_react(
      scratchpad,
      mod_tools_spec=tools_descriptor.TOOLS_SPECS,
      event_source_specs=events_descriptor.EVENT_SOURCE_SPECS,
  )

events/my_artifacts.py
  @artifact_namespace_rehoster(namespace="nmsp")
  async def rehost_nmsp_ref(...): ...
```

`events_descriptor.py` is a descriptor, not an auto-scan root. If the workflow
does not pass `EVENT_SOURCE_SPECS` into `build_react`, `react.pull` will not
know that the namespace exists. A module may also define
`list_artifact_namespace_rehosters()` and return decorated callables explicitly.

The rehoster must understand and apply ReAct artifact semantics:

- `files/...` is durable workspace/project state and is eligible for workspace
  history/publish in git mode.
- `snapshots/...` is story or wizard state. It is readable as an artifact and
  can be prefix-materialized only where the workspace backend supports that.
- `outputs/...` is a produced artifact area. It is not workspace state and is
  normally pulled by exact ref from hosted artifact metadata.
- `user.attachments/...` and `external.<event_kind>.attachments/...` are attachment
  surfaces and should be exact-file refs.

Use this destination map when writing the rehoster:

| Source artifact meaning | ReAct destination |
|---|---|
| Story/wizard state snapshot | `fi:turn_<id>.snapshots/<path>` / `turn_<id>/snapshots/<path>` |
| Evidence or domain attachment | `fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<name>` / `turn_<id>/external/<event_kind>/attachments/<event_id>/<name>` |
| Editable project/workspace file | `fi:turn_<id>.files/<workspace_scope>/<path>` / `turn_<id>/files/<workspace_scope>/<path>` |
| Produced report/export/rendered artifact | `fi:turn_<id>.outputs/<artifact_scope>/<path>` / `turn_<id>/outputs/<artifact_scope>/<path>` |

The returned `materialized` rows are the continuation contract. After pulling an
external ref, agents should continue from the returned paths.

For external evidence files, use the external attachment helpers instead of
pretending the file is a snapshot:

```python
artifact_id = "nmsp_<stable_hash>"
logical_path = build_external_attachment_logical_path(
    turn_id=turn_id,
    kind="case",
    message_id=artifact_id,
    relpath=f"nmsp/{key}",
)
physical_path = build_external_attachment_physical_path(
    turn_id=turn_id,
    kind="case",
    message_id=artifact_id,
    relpath=f"nmsp/{key}",
)
```

## Read / search / write responsibilities

`react.read`
- reads by logical path only
- supports the established turn-scoped forms:
  - `fi:<turn_id>.files/<subpath>`
  - `fi:<turn_id>.outputs/<subpath>`
  - `fi:<turn_id>.snapshots/<subpath>`
  - `fi:<turn_id>.user.attachments/<subpath>`
  - `fi:<turn_id>.external.<event_kind>.attachments/<event_id>/<subpath>`
  - `fi:conv_<conversation_id>.turn_<id>.files|outputs|snapshots/<subpath>`
- also supports any readable artifact-root file via:
  - `fi:<artifact-root-relative-path>`
  - example: `fi:turn_<id>/outputs/report.md`
- also supports exact `ks:` paths:
  - `ks:<relpath>`
  - example: `ks:<bundle-defined-path>`
- does not read owner-domain refs such as `nmsp:` directly. Pull the owner ref
  first and read the returned `fi:` logical path.

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
- `kind=file` writes are hosted with their full workspace-relative path preserved
- `visibility=external` emits the hosted file to the UI; `visibility=internal` keeps the hosted file available for later agent/runtime use without UI emission

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

`react.pull` materializes file bytes from hosted blob handles recorded in
timeline metadata. It must not treat a visible text preview as the complete file
content. Prefix pulls are metadata-driven and fetch exact hosted blobs; they are
not object-store scans and they do not extract execution workspace archives.

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
