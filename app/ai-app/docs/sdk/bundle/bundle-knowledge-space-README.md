---
id: ks:docs/sdk/bundle/bundle-knowledge-space-README.md
title: "Bundle Knowledge Space"
summary: "Optional bundle-defined read-only ks: namespace for React, plus exact-path reads, optional search, and optional exec-time browsing."
tags: ["sdk", "bundle", "knowledge", "react", "ks", "storage"]
keywords: ["knowledge space", "ks:", "knowledge_read_fn", "knowledge_search_fn", "resolve_namespace", "bundle storage", "on_bundle_load"]
see_also:
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-storage-cache-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
---
# Bundle Knowledge Space

`ks:` is an **optional**, bundle-defined, **read-only logical namespace** that a React agent can use.

Important constraints:
- `ks:` is **not mandatory**. A bundle may not expose it at all.
- The internal shape of `ks:` is **entirely bundle-defined**.
- `ks:` is **not** part of the turn `OUT_DIR`.
- `ks:` is **not** browsed by `react.search_files`.
- `ks:` may be readable by exact path, searchable, both, or neither, depending on what the bundle implements.

## Mental model

```text
Bundle-defined read-only namespace

  ks:<bundle-defined-path>

Examples of valid shapes a bundle may choose:
  ks:index.md
  ks:catalog/overview.md
  ks:repo/src/foo.py
  ks:assets/policies/iso27001.md

These are examples only, not platform-mandated folders.
```

The React agent should learn the actual `ks:` layout from:
- bundle skills
- bundle-specific search tools
- exact paths surfaced in prior results

## What React can do with `ks:`

### Exact-path read

If the bundle provides `knowledge_read_fn`, the agent can call:

```text
react.read(["ks:<bundle-defined-path>"])
```

This is the primary `ks:` contract.

### Optional search

If the bundle provides `knowledge_search_fn`, the bundle may expose a search surface such as `react.search_knowledge`.

Search is optional and bundle-defined:
- the bundle decides whether search exists
- the bundle decides what query params it supports
- the bundle decides how results map back to `ks:` paths

### Optional exec-time browsing

Normal React tools do **not** browse `ks:` as a directory tree.

Directory-style browsing is only possible if the bundle also exposes an **exec-only namespace resolver/helper** for generated code.

Typical pattern:
1. code starts from a logical ref such as `ks:<bundle-defined-root>`
2. bundle resolver returns an exec-local `physical_path`
3. generated code inspects descendants under that path
4. generated code emits follow-up logical refs such as `ks:<bundle-defined-root>/foo/bar.py`
5. later the agent uses `react.read(...)` on those logical refs

If no such resolver/helper exists, `ks:` is still exact-path readable, but not browseable as a filesystem tree.

## Recommended integration contract

### Required for `react.read`

Set:
- `runtime_ctx.knowledge_read_fn`

Expected role:
- accept a `ks:` logical path
- resolve it to bundle-owned content
- return text/base64/mime metadata in the shape expected by the React read path

### Optional for bundle search

Set:
- `runtime_ctx.knowledge_search_fn`

Expected role:
- search bundle knowledge
- return hits that can be turned into exact `ks:` reads

### Optional for exec-time browsing

Expose a bundle-local exec-only helper, for example:
- `bundle_data.resolve_namespace(logical_ref)`

This helper is not a platform-wide mandatory name. The `kdcube.copilot` bundle uses that pattern, but other bundles may choose a different helper or no helper at all.

## Where `ks:` can be backed from

`ks:` does **not** have to come from one specific storage backend.

Common backing choices:
- shared bundle local storage under `BUNDLE_STORAGE_ROOT`
- bundle-cloned repo/cache built in `on_bundle_load(...)`
- read-only files prepared from remote storage into local cache
- any other bundle-owned storage the resolver can expose safely

Recommended default:
- use shared bundle local storage for large read-only assets, indexes, cloned repos, and caches
- optionally map part of that storage into `ks:`

## Relationship to bundle storage

`ks:` is a **logical namespace**.
`BUNDLE_STORAGE_ROOT` is a **storage location**.

They are related, but not the same thing:
- a bundle may use local bundle storage to back `ks:`
- a bundle may expose only part of that storage via `ks:`
- a bundle may use local bundle storage without exposing any `ks:`

## Lifecycle

The usual pattern is:
1. `on_bundle_load(...)` prepares local read-only assets or indexes
2. bundle wires `knowledge_read_fn` and optional `knowledge_search_fn`
3. bundle skills teach the agent how to navigate the exposed namespace
4. generated exec code optionally uses a resolver/helper if directory-style browsing is needed

## Example bundle

Reference example:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05`

That bundle exposes one particular `ks:` layout for its own use case, but that layout is **example-specific**, not part of the general bundle contract.

## Checklist

- [ ] Decide whether the bundle exposes `ks:` at all.
- [ ] If yes, define the logical namespace shape in bundle docs/skills.
- [ ] Implement `knowledge_read_fn` for exact-path reads.
- [ ] Optionally implement `knowledge_search_fn`.
- [ ] Optionally expose an exec-only namespace resolver/helper for directory-style browsing.
- [ ] Keep `ks:` read-only from the React agent’s perspective.
