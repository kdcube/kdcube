---
id: ks:docs/sdk/agents/react/react-tools-README.md
title: "React Tools"
summary: "Current built-in react.* tool catalog, path contracts, and execution semantics."
tags: ["sdk", "agents", "react", "tools"]
keywords: ["react.read", "react.pull", "react.checkout", "react.write", "react.patch", "react.memsearch", "react.hide", "react.search_files", "react.plan"]
see_also:
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/react-round-README.md
  - ks:docs/sdk/agents/react/tool-call-blocks-README.md
  - ks:docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
---
# React Tools (react.*)

This document describes the current built-in `react.*` tool surface.

These tools are injected into the React decision runtime. They are not the same as:

- external tools such as `web_tools.*` or `rendering_tools.*`
- bundle tools
- exec runtime shell commands

## Common Rules

- All React tools are invoked through `action=call_tool`.
- A tool call appends `react.tool.call` and one or more `react.tool.result` blocks.
- If decision `notes` are present, the runtime emits a user-visible notes block before the tool call.
- React tools work with logical paths and current-turn workspace paths, not arbitrary host paths.
- Built-in React tools do not accept exec-only `physical_path` values returned from other tool results unless explicitly documented.

## Path Contracts

### Logical paths

Used for reading, hiding, or reopening existing artifacts:

- `fi:...` — files, outputs, attachments
- `ar:...` — timeline artifact aliases
- `so:...` — sources pool rows
- `su:...` — summary blocks
- `tc:...` — tool call / tool result records
- bundle-provided logical namespaces such as `ks:...`

### Current-turn workspace paths

Used for writing or patching new current-turn files:

- OUT_DIR-relative paths under the current turn
- for example `reports/summary.md` or `files/project/src/app.py` after normalization

Do not pass logical `fi:` paths to `react.write` or `react.patch`.

## Built-in Tool Catalog

### `react.read`

Reopens existing artifacts into the visible timeline.

- accepts logical paths only
- emits a status/result record first, then the reopened blocks
- deduplicates blocks already visible in the current context
- can reopen hidden blocks

Use it when the path already exists and React needs to inspect the content again.

### `react.pull`

Materializes historical `fi:` refs locally so code or later tools can use them by local path.

- accepts only `fi:<turn>.files/...` and exact attachment refs
- keeps historical material under its historical turn root
- does not seed the active current-turn workspace
- in `git` mode, hydrates text workspace slices from the git-backed lineage snapshot
- exact binary refs remain hosting-backed in both workspace modes

Use it for historical/reference material, not for defining what the current editable workspace should contain.

### `react.checkout`

Builds the active current-turn workspace from ordered historical `fi:<turn>.files/...` refs.

- `mode=replace` clears and rebuilds the current-turn workspace
- `mode=overlay` applies refs on top of what already exists
- later refs override earlier refs if they overlap

Use it when React needs a runnable/searchable/testable project tree in the current turn.

### `react.write`

Creates a new text artifact.

- accepts a current-turn relative `path`
- `channel=canvas|timeline_text|internal`
- `kind=display|file`
- always materializes the text artifact locally
- hosts and emits a downloadable file only when the artifact is external and `kind=file`
- `channel=internal` writes Internal Memory Beacons as `react.note`

Use it for text artifacts only. For PDFs, PPTX, DOCX, PNG, and other binary deliverables, use `rendering_tools.write_*` or exec tools.

### `react.patch`

Updates an existing current-turn file.

- accepts a current-turn relative `path`
- patch may be unified diff or full replacement text
- emits normal tool call/result blocks

Use it when the file already exists in the current-turn workspace and React wants an edit instead of a full rewrite.

### `react.memsearch`

Searches prior conversation history semantically.

- returns compact snippets and metadata
- `targets=["assistant"]` returns all visible `assistant.completion` blocks from a matched turn
- `targets=["summary"]` returns `conv.working.summary` blocks from a matched turn
- `targets=["attachment"]` covers both original turn attachments and event-scoped followup attachments
- event-scoped attachment paths use `fi:<turn>.external.<kind>.attachments/<message_id>/<filename>`
- use when relevant prior detail likely exists but is no longer visible

### `react.hide`

Replaces a visible tail block with a short placeholder.

- accepts logical paths such as `ar:`, `fi:`, `tc:`, `so:`, `ks:`
- original content remains recoverable with `react.read(path)`
- restricted to the editable tail window and cache checkpoint rules

Use it to shrink still-visible bulky material that is no longer needed in the active prompt.

### `react.search_files`

Searches safely under `outdir` or `workdir` without shell execution.

- rooted search only
- OUT_DIR hits include logical paths suitable for `react.read`
- workdir hits do not automatically become logical artifact paths

### `react.plan`

Creates or updates the plan tracked inside the same React loop.

- plan is a tool, not a separate planner component
- plan snapshots become timeline blocks and later rounds can update step state

## Bundle-Provided React-Style Tools

Some bundles also expose tools that fit the same interaction model but are not part of the built-in core set.

Examples:

- `react.search_knowledge`
  - bundle-provided knowledge search
- bundle-specific logical namespaces such as `ks:...`

Those are bundle contracts. The built-in React runtime only guarantees the core tools listed above.
