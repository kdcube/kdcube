---
id: ks:docs/sdk/agents/react/react-turn-workspace-README.md
title: "ReAct Turn Workspace"
summary: "Filesystem contract and lifecycle of the per-turn ReAct workspace (work/out), including local origin, runtime population, persistence, and Fargate/distributed snapshot transport."
tags: ["sdk", "agents", "react", "workspace", "execution", "snapshot", "fargate", "distributed"]
keywords: ["exec-workspace", "exec_YYYYMMDDHHMMSS", "workdir", "outdir", "timeline.json", "tool_calls_index.json", "user.log", "infra.log", "EXEC_SNAPSHOT", "build_exec_snapshot_workspace", "snapshot_exec_input", "py_code_exec_entry.py"]
see_also:
  - ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/turn-log-README.md
  - ks:docs/sdk/agents/react/source-pool-README.md
  - ks:docs/sdk/agents/react/external-exec-README.md
  - ks:docs/exec/distributed-exec-README.md
  - ks:docs/sdk/agents/react/design/files-vs-outputs-README.md
---
# ReAct Turn Workspace

This document defines the **actual workspace structure** used by ReAct and how it evolves across phases:
- local turn start (`exec-workspace/exec_<UTC timestamp>_<suffix>`)
- tool execution and artifact population
- optional turn snapshot persistence
- distributed/Fargate serialization, restore, and merge-back

The workspace is execution state. Canonical conversation state still lives in timeline/sources/turn-log artifacts.

Scope:
- this document describes the concrete workspace filesystem and lifecycle
- the current namespace separation between workspace files and non-workspace outputs is tracked in `design/files-vs-outputs-README.md`

## Effective agent workspace model

The agent does **not** perceive one flat filesystem. It reasons across several surfaces:

```text
VISIBLE / ADDRESSABLE WORKSPACE MODEL

1) CURRENT TURN ARTIFACT ROOT / OUTPUT_DIR (physical; current-turn execution surface)
   out/
     timeline.json
     tool_calls_index.json
     logs/
     ...                 # runtime metadata, tool-call JSON, diagnostics
     workdir/            # artifact root exposed as OUTPUT_DIR
       turn_<current_turn>/
         files/           # durable workspace/project namespace
         outputs/         # non-workspace produced artifacts
         attachments/     # current-turn attachments and rehosted copies pulled into this turn
   work/                  # exec scratch only; not stable collaboration state

   Agent-visible paths are relative to out/workdir:
     turn_<id>/files/...
     turn_<id>/outputs/...
     turn_<id>/attachments/...

2) CONVERSATION ARTIFACT MEMORY (logical; cross-turn; not a browsable folder)
   ar:...  tc:...  so:...  su:...
   fi:<older_turn>.files/...
   fi:<older_turn>.user.attachments/...

3) BUNDLE KNOWLEDGE SPACE `ks:` (logical; read-only virtual folder)
   ks:<bundle-defined-path>/...
   ...

```

Current behavior:
- History is preserved physically under `out/workdir/turn_<id>/files/...`, `out/workdir/turn_<id>/outputs/...`, and `out/workdir/turn_<id>/attachments/...`.
- Writes for the current turn go to:
  - `out/workdir/<current_turn>/files/...` for durable workspace/project state
  - `out/workdir/<current_turn>/outputs/...` for non-workspace produced artifacts
- Reads can target:
  - versioned turn artifacts and attachments
  - any readable artifact file already present under `out/workdir/`
  - exact logical `ks:` paths via `react.read`
- The practical mental model is:
  - `turn_<id>/files/...` and `turn_<id>/attachments/...` preserve origin and history
  - the latest visible version of a file path is the current logical workspace view
  - runtime folders like `logs/` are platform diagnostics under the sibling runtime root, not artifact paths
  - `ks:` is not inside the artifact root at all; it is a bundle-owned read-only virtual space

Workspace implementation (`RuntimeCtx.workspace_implementation`):
- `custom`
  - the agent is taught to use `fi:` plus `react.pull(paths=[...])` for historical materialization and `react.checkout(paths=[...])` for copying pulled `files/...` refs into the active current-turn workspace
  - `.files/...` pulls hydrate from artifact/timeline/hosting-backed snapshot state
  - the agent is not instructed to treat the activated workspace as git
- `git`
  - the agent is taught to use `fi:` plus `react.pull(paths=[...])` for historical materialization and `react.checkout(paths=[...])` for copying pulled `files/...` refs into the active current-turn workspace
  - `.files/...` pulls hydrate from git-backed lineage snapshots
  - the current turn root `out/workdir/<current_turn>/` is bootstrapped as a local git repo
  - that current-turn repo keeps lineage history available but does not eagerly populate the worktree
  - ANNOUNCE may show `previous saved workspace paths (pull to bring local; checkout to edit)` so React can see prior saved workspace paths without mistaking them for the current editable workspace
  - the agent may use local git inspection/history/edit commands inside that current-turn repo, except pull/push/fetch
- in both modes:
  - `fi:<turn_id>.files/<scope-or-subtree>` may be pulled as a subtree
  - `fi:<turn_id>.outputs/<file>` may be pulled as an exact file ref
  - `fi:<turn_id>.user.attachments/<name>` may be pulled only as an exact file ref
  - folder pulls do not imply hosted binaries; binary files must be named point-wise
  - in `git` mode, exact non-text `.files/...` refs that resolve to hosted artifacts are still hydrated from artifact/hosting history, not from git

### Knowledge space and exec-time path resolution

`ks:` is readable by logical path, for example `react.read(["ks:<bundle-defined-path>"])`.
Knowledge-space articles are uncapped only when no
`ai.react.knowledge_read_visible_*` cap is configured. When a cap is configured,
large `ks:` articles are recoverable by parts with `react.read` range items:

```json
{"paths":["ks:<bundle-defined-path>"],"stats_only":true}
{"items":[{"path":"ks:<bundle-defined-path>","line_start":1,"line_count":120}]}
```

Important constraints:
- `react.rg` does not browse `ks:`.
- `fetch_ctx` does not support `ks:`.
- `ks:` becomes a physical directory tree only inside isolated exec **if** the bundle exposes a namespace resolver/helper for it.

When such a resolver exists, the generated code flow is:
1. start from a logical ref such as `ks:<bundle-defined-root>`
2. call the bundle/helper resolver inside exec
3. receive an exec-local physical path
4. browse descendants in code for discovery only
5. emit discovered logical refs like `ks:<bundle-defined-root>/foo/bar.py` back into OUTPUT_DIR artifacts or short logs so the agent can later call `react.read` on them, including range reads when they are large

If the bundle does **not** expose a resolver for directory-style browsing, then `ks:` remains readable only by exact logical path or by bundle-specific search tools. It is not a normal browseable filesystem from standard React tools.

## Lifecycle at a glance

1. ReAct creates a fresh per-turn workspace directory (`exec_YYYYMMDDHHMMSS_ab12`, timestamped in UTC) with `work/` and `out/`.
2. During the turn, tools and runtime write files into `out/` (and sometimes `work/`).
   User-visible artifacts are under `out/workdir`; runtime metadata/logs stay directly under `out`.
3. Optionally, `react.persist_workspace()` stores zipped `out`/`work` for diagnostics.
4. For distributed/Fargate exec, a lightweight snapshot is built, uploaded, restored remotely, then outputs are merged back.

## Phase 1: Local origin workspace (turn start)

Workspace is created by `ContextBrowser._ensure_workspace()`.

Root resolution (`get_exec_workspace_root()`):
- `EXEC_WORKSPACE_ROOT` if set.
- Docker default: `/exec-workspace`.
- Host fallback: `HOST_EXEC_WORKSPACE_PATH`.
- Final fallback: `/tmp`.

Directory creation pattern:

```text
<exec_workspace_root>/
  exec_20260506125243_ab12/
    work/
    out/
```

Runtime bindings set immediately:
- `RuntimeCtx.workdir` / `RuntimeCtx.outdir` (`outdir` is the runtime metadata root)
- env vars: `WORKDIR`, `OUTPUT_DIR`
- context vars: `WORKDIR_CV`, `OUTDIR_CV`

Important:
- generated code and isolated rendering tools see `OUTPUT_DIR` as the artifact
  root, i.e. `out/workdir` in local runtime storage
- the platform uses the runtime output root `out` for metadata (`timeline.json`,
  `tool_calls_index.json`, tool-call JSON, logs, diagnostics)
- the agent should only use `turn_...` paths relative to `OUTPUT_DIR`; it should
  never use absolute host paths or `out/workdir` prefixes in tool params

When `workspace_implementation=git`:
- runtime also bootstraps `out/workdir/turn_<current_turn>/` as a local git repo
- if the lineage branch already exists, that repo starts from the latest lineage head
- runtime keeps the repo history/refs available but leaves the worktree empty until the agent explicitly materializes files
- if the lineage branch does not exist yet, runtime creates an empty orphan repo for the turn
- engineering, not exec, is responsible for later remote synchronization

## Phase 2: What is populated during a normal turn

### Regularly present files/folders in `out/`

`out/` is the main execution surface. Typical content:

```text
exec_20260506125243_ab12/
  work/
    main.py                                # stable loader executed by isolated runtime
    user_code.py                           # verbatim agent-generated program body/snippet
    ...                                    # helper files generated by execution code
  out/
    timeline.json                          # local timeline snapshot (written frequently)
    tool_calls_index.json                  # tool id -> list of persisted tool call files
    .tool_calls_index.lock                 # lock for index updates
    <safe_tool_id>-<timestamp>.json        # one per tool call payload
    result.json                            # present when generated runtime calls save_ret(...)
    delta_aggregates.json                  # delta cache dump from isolated supervisor/runtime
    executed_programs/                     # preserved executed program sources grouped per execution
      <execution_id>/
        main.py                            # platform loader as actually executed
        user_code.py                       # verbatim agent-generated program body/snippet
    workdir/                               # artifact root exposed as OUTPUT_DIR
      turn_<turn_id>/
        files/                             # turn-scoped file artifacts/rehosted files
        outputs/                           # non-workspace produced artifacts
        attachments/                       # turn-scoped attachment files
    logs/                                  # isolated runtime logs
      user.log                             # program/user stream (stdout/stderr + logger "user")
      infra.log                            # merged infra view for current execution id
      runtime.err.log                      # raw subprocess capture
      docker.out.log                       # raw outer docker stdout (docker mode)
      docker.err.log                       # raw outer docker stderr (docker mode)
      executor.log                         # executor process logger (mode-dependent)
      supervisor.log                       # supervisor process logger (mode-dependent)
```

Notes:
- Not every file appears on every turn; many are conditional on which tools/runtimes were used.
- `timeline.json` is flushed from the in-memory timeline to keep file-backed context in sync.
- Runtime diagnostics/readouts consume `logs/user.log` and `logs/infra.log`.
- `logs/infra.log` is produced by merging raw infra logs (`runtime.err.log`, `docker.*`, `executor.log`, `supervisor.log`) and may appear only after diagnostics/reporting code runs.
- The current platform-generated raw log set is: `user.log`, `runtime.err.log`, `docker.out.log`, `docker.err.log`, `executor.log`, `supervisor.log`, and derived `infra.log`.
- `runtime.out.log` appears in some older comments/docs but is not part of the current code path.
- `errors.log` is legacy/helper-only and is not part of the current main exec report assembly path.
- The merge step is generic over `*.log` files under `out/logs` except `user.log` and `infra.log`, so extra `.log` files can also appear in `infra.log` if future platform code or custom code writes them there.
- For exec/codegen runs, inspect `executed_programs/<execution_id>/user_code.py` first when debugging what the agent actually wrote. `executed_programs/<execution_id>/main.py` is the platform loader that bootstraps runtime globals and then executes `user_code.py`.

### Path conventions used inside the workspace

Logical `fi:` paths map to physical artifact-root paths by convention:
- `fi:<turn_id>.files/<rel>` -> `turn_<id>/files/<rel>`
- `fi:<turn_id>.user.attachments/<rel>` -> `turn_<id>/attachments/<rel>`
- legacy `fi:<turn_id>.attachments/<rel>` -> `turn_<id>/attachments/<rel>`
- `fi:<artifact-root-relative-path>` -> `<artifact-root-relative-path>` for readable files already present under `out/workdir/`

Other logical paths (`ar:`, `tc:`, `so:`) resolve from timeline state and are not always direct files.

Workspace/read-write summary:
- `react.write`, `react.patch`, rendering tools, and exec outputs may write to either:
  - `turn_<id>/files/...` for durable workspace state
  - `turn_<id>/outputs/...` for non-workspace produced artifacts
- unqualified `react.write` and exec contract paths default to `outputs/...`; use `files/...` explicitly for durable workspace/project state
- `react.read` can load any readable artifact-root file through `fi:...`.
- `react.pull` materializes selected `fi:` snapshot refs locally under the artifact root as historical/reference material.
- `react.checkout` copies selected historical `files/...` refs into the active current-turn `files/` workspace so they can be modified there.
- `.files/...` pulls come from:
  - artifact/timeline/hosting-backed snapshot state in `custom`
  - git-backed lineage snapshots in `git`
- `.outputs/...` pulls always come from artifact/timeline/hosting-backed snapshot state
- exact attachment pulls still come from hosted artifact storage in both modes
- exact non-text `.files/...` refs also stay on the hosted/artifact path when timeline metadata says the file is a hosted binary artifact
- `react.pull` supports subtree pulls only for `fi:<turn_id>.files/...`; `fi:<turn_id>.outputs/...` and attachment/binary pulls must be exact file refs
- `react.checkout(mode="replace")` accepts ordered `fi:<turn_id>.files/...` refs after pull and replaces `turn_<current>/files/` before applying them
- `react.checkout(mode="overlay")` accepts ordered `fi:<turn_id>.files/...` refs after pull and applies them into the existing current workspace without deleting unspecified files
- exec/code no longer auto-materialize historical workspace files, and `react.patch` never edits historical paths directly; if the file is not already local, React must `react.pull(...)` it first, then `react.checkout(...)` historical `files/...` refs before editing
- when continuing the same project, React is expected to reuse the existing top-level `files/<scope>/...` folder rather than inventing a sibling scope
- if the old scope name is clearly weak or misleading, React may intentionally rename/migrate the project tree to a better canonical scope
- a rename is different from sibling drift: the project should continue under the new scope instead of leaving the old scope active and starting a second one
- `react.rg` can search readable files already materialized in the local artifact workspace and returns `logical_path` so the agent can immediately call `react.read`. For content matches it also returns `read_item` ranges for exact `react.read({"items":[...]})` inspection.
- Preferred `react.rg` roots are visible path forms: `files/...`, `outputs/...`, `attachments/...`, `turn_<id>/files/...`, `turn_<id>/outputs/...`, `turn_<id>/attachments/...`, or matching `fi:` artifact paths such as `fi:<turn_id>.files/...`, `fi:<turn_id>.outputs/...`, and `fi:<turn_id>.user.attachments/...`. Legacy `outdir/...` roots are accepted only for compatibility.
- `react.rg` is not a search over the endless/pruned conversation timeline or unpulled historical snapshots. If the needed file is older state, React must identify the `fi:` ref, then `react.pull` it before local search. Checkout is only for making an editable current-turn copy.
- `work/` is internal execution scratch and is not part of the normal React search/read contract.

## Phase 3: Optional turn snapshot persistence (`react.persist_workspace()`)

If `REACT_PERSIST_WORKSPACE` is enabled (default on), ReAct stores zipped diagnostics via `ConversationStore.put_execution_snapshot(...)`:

```text
cb/tenants/<tenant>/projects/<project>/executions/
  <user_type>/<user_or_fp>/<conversation_id>/<turn_id>/<codegen_run_id>/
    out.zip
    pkg.zip
```

Meaning:
- `out.zip` = snapshot of `out/`
- `pkg.zip` = snapshot of `work/`

This is for diagnostics/forensics, not canonical conversation reconstruction.

## Phase 4: Distributed/Fargate workspace serialization and restore

Local split Docker execution keeps the same distinction as remote snapshots:
the runtime output root owns `timeline.json`, sources, logs, and runtime
metadata, while the artifact root (`out/workdir` locally, `/workspace/out` in
the executor container) is exposed to generated code as `OUTPUT_DIR`.
`ctx_tools.fetch_ctx` reads logical timeline refs from the runtime output root
first and only falls back to the artifact root for legacy layouts.

### 4.1 Host-side lightweight snapshot build

Before remote execution, host builds a reduced workspace (`build_exec_snapshot_workspace(...)`):
- copy full `work/`
- create filtered `out/timeline.json`
- include only referenced files required by code (`fetch_ctx`/file refs)
- if any referenced file belongs to a git-backed turn root, copy the whole artifact-root `turn_<id>/` tree so `.git` survives in isolated exec
- write `out/exec_snapshot_manifest.json`

Temporary snapshot tree:

```text
/tmp/exec_ws_<random>/
  work/
    ... (copy of local workdir)
  out/
    timeline.json                          # filtered to referenced paths
    exec_snapshot_manifest.json            # included paths summary
    workdir/
      <referenced turn_... artifact files only>
```

### 4.2 Storage layout for remote execution

`snapshot_exec_input(...)` uploads snapshot zips to execution-scoped keys:

```text
cb/tenants/<tenant>/projects/<project>/executions/
  <user_type>/<user_or_fp>/<conversation_id>/<turn_id>/<codegen_run_id>/<exec_id>/
    input/work.zip
    input/out.zip
    output/work.zip
    output/out.zip
```

`runtime_globals["EXEC_SNAPSHOT"]` carries these URIs into remote runtime.

Delta packaging conventions (`distributed_snapshot.py` defaults):
- skipped directories: `logs/`, `executed_programs/`, `__pycache__/`, `.pytest_cache/`, `.git/`
- skipped files: `sources_pool.json`, `sources_used.json`, `tool_calls_index.json`
- upload is baseline-aware (only changed files since restore are included)

### 4.3 Remote executor workspace (inside Fargate task)

Container runtime paths:

```text
/workspace/
  work/                                    # restored from input/work.zip
  runtime-out/                             # runtime metadata/logs in split mode
  out/                                     # artifact root exposed as OUTPUT_DIR inside the executor
  bundles/<bundle_dir>/                    # optional bundle restore from BUNDLE_SNAPSHOT_URI
```

`py_code_exec_entry.py` flow:
1. restore input snapshot zips into `/workspace/work` and `/workspace/out`
2. bootstrap supervisor/tool runtime with restored globals and bundle path rewrites
3. execute code
4. upload output zips (`output/work.zip`, `output/out.zip`) as deltas

### 4.4 Merge-back conventions on host after Fargate completion

Host merge behavior (`external/fargate.py`):
- `output/work.zip` -> extracted directly into local `workdir`
- `output/out.zip` -> extracted to temp dir, then selective merge:
  - append `logs/*`
  - copy artifact trees with top-level folder `turn_*` into the local artifact root
  - do **not** overwrite `timeline.json` / `sources_pool` directly

This preserves local timeline/sources authority while still importing generated turn artifacts.

## Phase 5: What is canonical vs diagnostic

Canonical conversation state:
- timeline artifacts
- sources pool artifacts
- turn log artifacts
- stored messages/attachments/files

Workspace (`work/` + `out/`) is execution state and diagnostics. Use it for runtime debugging and snapshot transport, not as a long-term source of truth.

## Code map

Primary implementation points:
- workspace origin: [`kdcube_ai_app/apps/chat/sdk/solutions/react/browser.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/browser.py)
- root selection: [`kdcube_ai_app/apps/chat/sdk/solutions/infra.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/infra.py)
- tool call index/files: [`kdcube_ai_app/apps/chat/sdk/runtime/tool_index.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/tool_index.py), [`kdcube_ai_app/apps/chat/sdk/tools/io_tools.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tools/io_tools.py)
- local timeline file writes: [`kdcube_ai_app/apps/chat/sdk/solutions/react/timeline.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/timeline.py)
- lightweight distributed snapshot: [`kdcube_ai_app/apps/chat/sdk/solutions/react/solution_workspace.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/solution_workspace.py)
- snapshot upload/path conventions: [`kdcube_ai_app/apps/chat/sdk/runtime/external/distributed_snapshot.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/external/distributed_snapshot.py)
- remote restore/upload entrypoint: [`kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py)
- Fargate launch + merge-back: [`kdcube_ai_app/apps/chat/sdk/runtime/external/fargate.py`](../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/external/fargate.py)
