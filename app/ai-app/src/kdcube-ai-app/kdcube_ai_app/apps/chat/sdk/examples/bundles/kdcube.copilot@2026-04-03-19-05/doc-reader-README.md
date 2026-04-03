---
title: "KDCube Copilot Bundle — Doc Reader Flow"
summary: "Reference bundle that exposes docs + code through the ks: knowledge space and React search/read tools."
tags: ["bundle", "react", "knowledge", "docs", "reader"]
keywords: ["knowledge space", "ks:", "react.read", "react.search_knowledge", "bundle_storage", "on_bundle_load"]
---

# KDCube Copilot Bundle — Doc Reader Flow

This bundle is a **documentation reader** that exposes the platform docs and related
source files through the React knowledge space (`ks:`). It is intended for **product
and architecture Q&A** where answers must cite internal docs and code references.

Motivation:
- keep docs/source browsing deterministic (no ad‑hoc file paths in prompts)
- allow React to search & read a curated set of files via `ks:` paths
- expose deployment + source references alongside docs

## How it works (high‑level)

1) **Knowledge space is prepared before each turn (cached)**
   - Entry: `entrypoint.py` calls `_ensure_knowledge_space()` in `pre_run_hook`.
   - A signature cache prevents rebuilding unless repo/ref/roots change.
   - The builder scans `docs/` for front‑matter and builds:
     - `index.json` (structured list of docs)
     - `index.md` (human‑readable list)
   - Files: `knowledge/index_builder.py`, `knowledge/resolver.py`.

2) **Knowledge source (repo + common root)**
The docs, sources, deployment artifacts, UI files, and test fixtures are pulled from a git repo defined in **bundle props**:

Required bundle props:
- `knowledge.repo`: git URL of the repo that contains docs and source code.
- `knowledge.ref`: git tag/commit/branch (tag/commit recommended for deterministic releases).
- `knowledge.root`: common knowledge root inside the repo (relative to repo root). For this repo that root is `app/ai-app`.

The bundle prepares `ks:` from that one common root. Real logical paths are then derived by prepending `ks:` to the app-relative path:
- `ks:docs/...`
- `ks:deployment/...`
- `ks:src/...`
- `ks:ui/...`

These are examples of real app-relative paths under one common root, not special platform-mandated subnamespaces.

If **all** `knowledge.*` fields are empty, the bundle tries to auto‑detect a local
repo (host dev) or falls back to `KDCUBE_KNOWLEDGE_REPO` (default: public repo).

Example (this repo):
```yaml
knowledge:
  repo: git@github.com:kdcube/kdcube-ai-app.git
  ref: <tag-or-sha>
  root: app/ai-app
  validate_refs: true
```

The repo is cloned into **bundle local storage** under this bundle's own storage subtree:
```
<bundle_storage>/repos/<repo>__<bundle_id>__<ref>/
```

Then `knowledge.root` is resolved against that repo root.

3) **Knowledge space layout**
```
<knowledge_root>/
  docs/            # symlink or copy of app/ai-app/docs
  deployment/      # symlink or copy of app/ai-app/deployment
  src/             # symlink or copy of app/ai-app/src
  ui/              # symlink or copy of app/ai-app/ui
  index.json
  index.md
```

For this bundle specifically:
- `self.bundle_storage_root()` returns the bundle's per-bundle storage directory
- `entrypoint.py` passes that path as `knowledge_root=ws_root`

So in `kdcube.copilot`:
- `knowledge_root == self.bundle_storage_root()`
- but `knowledge_root` is **not** the same thing as the shared `BUNDLE_STORAGE_ROOT`
- it is one bundle-specific subtree under that shared root

That subtree contains both:
- the built knowledge-space files (`docs/`, `deployment/`, `src/`, `ui/`, `index.json`, `index.md`)
- and helper storage such as `repos/...` used to clone/fetch the source repository

In external isolated execution, this directory is now transported too:

- Docker external exec: the per-bundle storage dir is bind-mounted read-only into the child container
- Fargate external exec: the per-bundle storage dir is snapshotted and restored into the exec task

Inside isolated exec, `BUNDLE_STORAGE_DIR` points at that physical directory.
`knowledge/resolver.py` falls back to `BUNDLE_STORAGE_DIR` when `KNOWLEDGE_ROOT`
has not already been initialized by the main bundle entrypoint.

### Where the data is physically

This bundle uses three distinct physical areas during external exec:
- `workdir` — generated Python program and scratch runtime files
- `outdir` — writable outputs and logs
- bundle storage / `knowledge_root` — readonly knowledge space data for the child runtime

#### Path matrix

| Area | Host-side root in Docker-on-EC2 mode | `chat-proc` visible path | Isolated exec visible path | Notes |
| --- | --- | --- | --- | --- |
| workdir | `HOST_EXEC_WORKSPACE_PATH/ctx_<id>/work` | `EXEC_WORKSPACE_ROOT/ctx_<id>/work` | `WORKDIR=/workspace/work` | generated `main.py`, scratch files |
| outdir | `HOST_EXEC_WORKSPACE_PATH/ctx_<id>/out` | `EXEC_WORKSPACE_ROOT/ctx_<id>/out` | `OUTPUT_DIR=/workspace/out` | contract files, logs, execution results |
| shared bundle-storage root | `HOST_BUNDLE_STORAGE_PATH` | `BUNDLE_STORAGE_ROOT` | not directly exposed as an agent contract | shared parent root |
| `kdcube.copilot` per-bundle subtree | `HOST_BUNDLE_STORAGE_PATH/<bundle-subdir>` | `BUNDLE_STORAGE_ROOT/<bundle-subdir>` | `BUNDLE_STORAGE_DIR=<same bundle subdir path>` | readonly in external exec |

Notes:
- `knowledge_root` for this bundle is that per-bundle storage subtree.
- The exact `<bundle-subdir>` is not a public agent contract. Bundle/runtime code computes it.
- Inside that subtree, `kdcube.copilot` stores both `repos/...` and the built knowledge-space files.
- In external exec, the child should treat `BUNDLE_STORAGE_DIR` as readonly.

#### What `kdcube.copilot` stores in that subtree

```text
<per-bundle-subtree>/              # this bundle's knowledge_root
  repos/
    <repo>__kdcube.copilot.knowledge__<ref>/...
  docs/
  deployment/
  src/
  ui/
  index.json
  index.md
```

#### Docker external exec on ECS/EC2

```mermaid
flowchart LR
    subgraph H["Host filesystem"]
        HWD["HOST_EXEC_WORKSPACE_PATH/ctx_<id>/work"]
        HOD["HOST_EXEC_WORKSPACE_PATH/ctx_<id>/out"]
        HBS["HOST_BUNDLE_STORAGE_PATH"]
        HKS["HOST_BUNDLE_STORAGE_PATH/<bundle-subdir>"]
    end

    subgraph P["chat-proc container"]
        PWD["EXEC_WORKSPACE_ROOT/ctx_<id>/work"]
        POD["EXEC_WORKSPACE_ROOT/ctx_<id>/out"]
        PBS["BUNDLE_STORAGE_ROOT"]
        PKS["BUNDLE_STORAGE_ROOT/<bundle-subdir> = knowledge_root"]
    end

    subgraph X["Isolated exec sandbox"]
        XWD["/workspace/work  (WORKDIR)"]
        XOD["/workspace/out   (OUTPUT_DIR)"]
        XKS["BUNDLE_STORAGE_DIR  (readonly knowledge_root)"]
    end

    HWD --> PWD --> XWD
    HOD --> POD --> XOD
    HBS --> PBS
    HKS --> PKS --> XKS
```

Interpretation:
- `workdir` and `outdir` are rebased into `/workspace/...` inside the sandbox.
- `BUNDLE_STORAGE_ROOT` is the shared parent root in `chat-proc`.
- `knowledge_root` is the `kdcube.copilot` per-bundle subtree under that parent root.
- `knowledge_root` is not rebased into `OUTPUT_DIR`.
- The knowledge space is exposed as its own readonly subtree through `BUNDLE_STORAGE_DIR`.

#### Fargate external exec

In Fargate mode there is no shared host bind mount into the child runtime.
Instead:
- `workdir` is snapshotted and restored into `/workspace/work`
- `outdir` is snapshotted and restored into `/workspace/out`
- bundle storage / `knowledge_root` is snapshotted and restored into `BUNDLE_STORAGE_DIR`

So the child runtime still sees the same three classes of data:
- scratch `workdir`
- writable `outdir`
- readonly knowledge space

4) **Path scheme**
- `ks:index.md` — short index for navigation.
- Any real `app/ai-app`-relative path can be read as `ks:<relative path>`.
- Common examples in this bundle:
  - `ks:docs/<path>` — doc pages (Markdown).
  - `ks:src/<path>` — source files under the real `app/ai-app/src` tree.
  - `ks:deployment/<path>` — deployment files under the real `app/ai-app/deployment` tree.
  - `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/<path>` — bundle pytest files under the real source tree.

### Advertised roots for this bundle

These are the intended starting points this bundle advertises to the agent.
They are real app-relative paths under one common `ks:` root, not special platform namespaces.
The search/index rules are separate from the namespace rules:
- `ks:src/...` paths are still normal paths under the same common `ks:` root
- but `react.search_knowledge(...)` currently indexes docs metadata and deployment markdown, not the whole `ks:src/...` tree

| Logical base                                                      | Intended content                         | `react.search_knowledge`  | `react.read`                     | Exec browsing via `bundle_data.resolve_namespace(...)` |
|-------------------------------------------------------------------|------------------------------------------|---------------------------|----------------------------------|--------------------------------------------------------|
| `ks:docs`                                                         | platform docs                            | yes                       | yes                              | yes                                                    |
| `ks:deployment`                                                   | deployment files and deployment markdown | deployment markdown only  | yes                              | yes                                                    |
| `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`                | SDK source                               | no                        | yes, when exact path is known    | yes                                                    |
| `ks:src/kdcube-ai-app/kdcube_ai_app/apps/infra`                   | infra source                             | no                        | yes, when exact path is known    | yes                                                    |
| `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle` | bundle pytest suite                     | no                        | yes, when exact path is known    | yes                                                    |

Guidance:
- Use `react.search_knowledge(...)` first for docs and deployment markdown.
- Use exact `react.read(...)` when a doc or prior result already gives you the concrete `ks:` path.
- Use exec-time namespace resolution only when you need directory-style browsing under one of the advertised roots.

5) **Doc ↔ code resolution**
Docs may reference code like:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/runtime.py`
The agent should convert that directly to:
`ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/runtime.py`

## How React uses it

React tooling (bundle‑provided):
- `react.search_knowledge(query, root="ks:docs")` — search docs (metadata search).
- `react.read(["ks:docs/<path>"])` — open a doc.
- `react.read(["ks:src/<path>"])` — open a source file.
- `react.search_knowledge(query, root="ks:deployment")` — search deployment docs.
- `react.read(["ks:deployment/<path>"])` — open deployment files.
- `react.read(["ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/<path>"])` — open exact bundle pytest files or suite guidance.
- `bundle_data.resolve_namespace(logical_ref)` — exec-only resolver for generated code. Returns `{ok, error, ret}` where `ret` is `{physical_path: str | null, access: 'r' | 'rw', browseable: bool}`.
  - `physical_path` is usable only inside isolated exec.
  - use the input `logical_ref` itself as the logical base for later `react.read(...)` follow-up.
You can optionally pass `keywords=[...]` to `react.search_knowledge` to bias ranking
toward specific tags or terms.

Important:
- `bundle_data.resolve_namespace(...)` is **not** a normal planning-time browsing tool.
- It is intended only for generated Python running inside `execute_code_python(...)`.
- Outside isolated exec it returns an error by design.
- Because this tool runs only inside generated exec code, the agent sees its result only if that code propagates it out:
  - write a summary/result file under `OUTPUT_DIR/...`
  - and/or print short diagnostics to `user.log`
- Practical pattern:
  - set `logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk"`
  - resolve `logical_base`
  - inspect files under the returned `physical_path`
- if code finds a useful file at relative path `runtime/execution.py`, emit logical ref `f"{logical_base}/runtime/execution.py"`
- the agent can later call `react.read(["ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/execution.py"])`
- the same rule applies for test browsing:
  - keep `logical_base = "ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle"`
  - browse descendants under the exec-only `physical_path`
  - emit follow-up logical refs as `f"{logical_base}/{relative_path}"`
- For the exact propagation model, see:
  - `ks:docs/exec/exec-logging-error-propagation-README.md`

The **product skill** (`skills/product/kdcube/SKILL.md`) tells the agent to:
1) Read `ks:index.md` for entry points.
2) Search + read docs from `ks:docs/...`.
3) Prefer the advertised roots above as browsing start points.
4) Read referenced code/deploy files via exact `ks:` paths when the mapping is obvious.
5) If the exact source/deploy path is unclear, use generated exec code plus `bundle_data.resolve_namespace(...)` to browse the real subtree and emit the exact follow-up `ks:` refs before calling `react.read(...)`.

## Bundle-authoring cross-reference

This knowledge bundle is the narrow reference for `ks:` and namespace resolution.
For normal bundle authoring, the primary source example remains `versatile`.

Important cross-links:
- `ks:docs/sdk/bundle/bundle-reference-versatile-README.md`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx`

That `ui-src/src/App.tsx` file is the current lightweight reference for a bundle-defined
custom main view built through `ui.main_view`. It shows the iframe config handshake,
SSE + REST chat flow, bundle-scoped chat list loading, downloads, followups, and
separate timeline/steps/downloads presentation.

Tool registration:
- The bundle defines `react.search_knowledge` in `tools/react_tools.py`.
- The bundle defines `bundle_data.resolve_namespace` in `tools/exec_space_tools.py`.
- Tools are registered via `tools_descriptor.py` with alias `react`.
  The exec-only resolver is registered separately with alias `bundle_data`.

## Search resolver (how it works)

`react.search_knowledge` is backed by a simple resolver in:
`knowledge/resolver.py`.

Behavior (current):
- Loads `index.json` generated at startup.
- Filters items by `root`:
  - If `root="ks:docs"` then only paths starting with `ks:docs` are searched.
  - If `root` is omitted, all indexed items are searched.
- Performs **hybrid metadata search** (case‑insensitive) across:
  - `title`
  - `summary`
  - `tags` + `keywords`
  - `path`
- Ranks hits by weighted signals:
  - Title phrase match (highest)
  - Tag/keyword match
  - Summary match
  - Path match (lowest)
- Returns `path`, `title`, and `score` (no full‑text search yet).

This means search is **metadata‑level**, not content‑level.
For precise answers, the agent must open docs via `react.read(...)`.

## Read + search flow (visual)

```mermaid
flowchart LR
    A[Bundle Startup] --> B[Pull docs repo]
    B --> C[Build index.json + index.md]
    C --> D[Expose common-root-relative ks: paths]

    U[User question] --> S[React reads skill]
    S --> I[react.read ks:index.md]
    I --> Q[react.search_knowledge]
    Q --> R[react.read ks:docs/<path>]
    R --> X[optional: react.read ks:src/<real-path>]
```

## What’s still missing / TODO

1) **Semantic search** for knowledge space (current search is lexical).
2) **Auto‑refresh** of the index when docs change (currently on startup).
3) **DB/graph knowledge resolvers** (Postgres / Neo4j / hybrid).
4) **Explicit “doc roots”** beyond `docs/` (e.g., product specs, ADRs).
5) **Structured doc metadata enforcement** (validate required front‑matter fields).
6) **External link validation** (only code refs are validated today).

## Relevant implementation files

- `kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/knowledge/index_builder.py`
- `kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/knowledge/resolver.py`
- `kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/tools/react_tools.py`
- `kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/entrypoint.py`
- `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/read.py`

## EC2 docker‑compose: what you need to add for the doc bundle

1. Create host dir:
```shell
mkdir -p /path/to/bundle-storage
```

2. In compose .env:
```shell
HOST_BUNDLE_STORAGE_PATH=/path/to/bundle-storage
BUNDLE_STORAGE_ROOT=/bundle-storage
```

3. In .env.proc:
```shell
BUNDLE_STORAGE_ROOT=/bundle-storage
```

4. Ensure proc can write (index build happens on startup):
```shell
sudo chown -R 1000:1000 /path/to/bundle-storage
# or
sudo chmod -R 0777 /path/to/bundle-storage
```
