---
id: ks:docs/sdk/agents/react/external-exec-README.md
title: "External Exec"
summary: "External execution flow for React (snapshots, remote runners)."
tags: ["sdk", "agents", "react", "exec", "external"]
keywords: ["snapshot", "external exec", "fargate", "distributed execution"]
see_also:
  - ks:docs/sdk/agents/react/event-blocks-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/artifact-storage-README.md
  - ks:docs/exec/exec-logging-error-propagation-README.md
  - ks:docs/exec/distributed-exec-README.md
---
## External execution notes (Fargate / distributed)

This page focuses on the React-agent view of external execution.

For the runtime/deployment internals, see:

- [exec-logging-error-propagation-README.md](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/docs/exec/exec-logging-error-propagation-README.md)
- [distributed-exec-README.md](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/docs/exec/distributed-exec-README.md)

### Bundle code vs bundle readonly data

External exec transports two different bundle-side inputs:

- bundle code root
- per-bundle readonly storage dir

The bundle code root is where bundle-local tools live, for example bundle-relative paths like:

- `tools/react_tools.py`
- `knowledge/resolver.py`

The per-bundle readonly storage dir is where bundles keep prepared local data such as:

- cloned docs repos
- built indexes
- cached read-only knowledge space files

Inside isolated exec, that directory is exposed at `BUNDLE_STORAGE_DIR`.

Current transport behavior:

- Docker external exec:
  - bundle code root is mounted read-only
  - `BUNDLE_STORAGE_DIR` is mounted read-only
- Fargate external exec:
  - bundle code root is snapshotted and restored
  - `BUNDLE_STORAGE_DIR` is snapshotted and restored

Example: `kdcube.copilot`

- bundle code lives under `/bundles/kdcube.copilot@...`
- its built knowledge space lives under the per-bundle storage dir
- `react.search_knowledge(...)` reads that physical knowledge space through `knowledge/resolver.py`
- if isolated exec loads the resolver without having run the bundle entrypoint first, the resolver falls back to `BUNDLE_STORAGE_DIR`

### What the agent calls

The public tool is `exec_tools.execute_code_python(...)` in [exec_tools.py](/Users/elenaviter/src/kdcube/kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tools/exec_tools.py).

Current public contract:

- code is provided in the dedicated code channel, not in tool params
- `contract` is required
- `contract` must be a non-empty list (or JSON string) of files to produce
- each contract file must live under `turn_<id>/files/...`
- each contract item supports:
  - `filename`
  - `description`
  - optional `visibility` = `external|internal` (default: `external`)

Important current limitation:

- the public `execute_code_python` tool does **not** currently expose a logs-only / empty-contract mode
- an empty or missing contract is rejected at normalization time
- there is internal support for no-contract execution via `run_exec_tool_no_contract(...)` and `run_exec_tool_side_effects(...)`, but that is not the public React tool surface today

### What the agent gets back

Independent of runtime mode (`docker`, `fargate`, or local isolated execution), the tool returns the same logical envelope:

- `ok`
- `artifacts`
- `error`
- `report_text`
- `items`
- `user_out_tail`
- `runtime_err_tail`

The most important agent-facing field is `report_text`.

`report_text` is the final human-readable execution summary assembled by `exec_tools.py`.
It is not a raw file.

It can include:

- final status line
- runtime failure summary
- missing contracted outputs
- artifact validation failures
- infra/runtime error lines
- `Program log (tail)` from `out/logs/user.log`

So even in the normal file-producing mode, the agent already receives a hybrid result:

- declared output files if they were produced
- plus log/diagnostic text from the execution

That means the tool is not "files only". It is "contracted files, with logs folded into the textual result".

Visibility rules for contracted files:

- `visibility=external`
  - eligible for hosting / RN emission
  - shown to the user as a produced file artifact
- `visibility=internal`
  - kept in OUT_DIR and timeline for agent/runtime use
  - not hosted
  - not emitted to the user as a file attachment

### Exactly how the agent should write code if it wants logs to appear

If the agent wants its runtime progress or notes to appear in the execution result, it should write code that emits to `user.log`.

Use `OUTPUT_DIR` as the primary runtime root.
`OUT_DIR` is simply `Path(OUTPUT_DIR)` if Path operations are more convenient.

Normal pattern:

```python
from pathlib import Path

out_path = Path(OUTPUT_DIR) / "turn_123/files/result.json"
```

For `user.log`:

- normal `print(...)` goes there
- uncaught exceptions / tracebacks go there
- depending on config, Python `logging` may also go there

The most reliable pattern is:

```python
print("starting step 1")
print(f"rows loaded: {len(rows)}")
print("done")
```

If the agent wants structured log lines, the safest form is:

```python
import logging

log = logging.getLogger("user")
log.info("starting batch job")
log.warning("row 42 skipped")
```

That `user` logger is explicitly wired to `user.log`.

Important nuance:

- generic `logging.getLogger(__name__)` may also land in `user.log` when `EXEC_USER_LOG_MODE=include_logging`
- but if config changes to `print_only`, generic logging may stay out of `user.log`
- `print(...)` and `logging.getLogger("user")` are the stable choices

So the concrete guidance for React agents is:

1. If you need files as outputs, declare them in `contract` and write them under `OUTPUT_DIR/turn_<id>/files/...`.
2. If you want the model to later see progress/details in the textual tool result, use `print(...)` and/or `logging.getLogger("user")`.
3. Do not assume that generic `logging.getLogger(__name__)` will always appear in `Program log (tail)`.
4. Remember that the current public `execute_code_python(...)` tool still requires a non-empty file contract, even though the returned result already includes logs.

### Concrete recommendation for copilot-style exec tasks

If the agent wants to use exec as a copilot-style helper for tasks such as:

- list files
- search for content
- inspect a workspace
- compute a patch
- summarize filesystem state

then the agent should **not** treat stdout as the authoritative output channel.

Current immediate-result limits:

- `Program log (tail)` is only the tail of `out/logs/user.log` and is currently capped by `USER_LOG_TAIL_CHARS = 4000`
- text file previews in returned artifacts are currently capped by `EXEC_TEXT_PREVIEW_MAX_BYTES = 20000`

So the concrete pattern should be:

1. Put the authoritative result into contracted files.
2. Use `print(...)` or `logging.getLogger("user")` only for a short summary:
   - counts
   - high-level status
   - names of produced files
3. If the result may be large, split it into several files so each one stays readable in the immediate tool result.
4. Prefer structured outputs over giant free-form dumps.

Recommended file patterns:

- filesystem listing / inventory:
  - `listing.json`
  - `summary.txt`
- content search / grep-like scan:
  - `matches.json`
  - `matches.txt`
- code edit / patch proposal:
  - `changes.diff` or `changes.patch`
  - `changes_summary.json`
- diagnostics / inspection:
  - `report.json`
  - `summary.txt`

Concrete snippet pattern:

```python
import json
import logging
from pathlib import Path

log = logging.getLogger("user")
root = Path("..")

rows = []
for path in sorted(root.rglob("*.py")):
    rows.append({"path": str(path)})

out_path = Path(OUTPUT_DIR) / "turn_123/files/listing.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

print(f"scanned {len(rows)} files")
log.info("wrote listing.json")
```

That pattern is correct because:

- the full result is in a contracted file
- the program log stays short
- the tool result can still show a useful summary immediately

If the agent wants the result "fully" in one immediate tool response, the current public surface has a practical limit:

- logs are tail-only
- large text artifacts are preview-truncated

So for large results the best current strategy is to shard the output into multiple files and keep each file reasonably small.

### Logs-only and hybrid modes

Current state:

- public mode: contract required, files expected, logs included in diagnostics
- internal no-contract mode: supported by `run_exec_tool_no_contract(...)`
- internal side-effects mode: supported by `run_exec_tool_side_effects(...)`

So the platform already has the building blocks for:

- logs-only execution
- hybrid execution with both files and `user.log`-derived output

But today only the hybrid-with-required-contract variant is exposed to the React agent through `execute_code_python`.

### What is still missing for a more convenient copilot-style interface

The current public tool is usable, but two ergonomic gaps remain:

1. There is no public logs-only / empty-contract mode for simple "run and show me the output" tasks.
2. There is no special public mode that says "treat this one text artifact as the primary inline result".

So today the safest public contract for agentic/copilot-like exec is still:

- always declare files
- write the authoritative result to those files
- keep logs brief

### Snapshot + merge flow
- Host creates input snapshot via `snapshot_exec_input`  
  `kdcube_ai_app/apps/chat/sdk/runtime/external/distributed_snapshot.py`
- Snapshot layout:  
  `cb/tenants/<tenant>/projects/<project>/executions/<user_type>/<user_or_fp>/<conversation>/<turn>/<run_id>/<exec_id>/`
    - `input/work.zip`, `input/out.zip`
    - `output/work.zip`, `output/out.zip`
- Input snapshot uses a **lightweight exec workspace**:
  - `work/` copied fully
  - `out/` contains filtered `timeline.json` + only referenced files (from code + fetch_ctx)
  - Manifest: `exec_snapshot_manifest.json`
- Remote executor restores `input/*` before supervisor bootstrap in  
  `kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`
- Remote executor uploads `output/*` after execution (delta-aware).
- Host (Fargate runner) restores `output/*` into local `workdir/outdir` after task finishes.
  - Outdir merge is **selective**: only `turn_*` outputs copied, `logs/` appended.
  - `timeline.json` / `sources_pool.json` are preserved.

### Delta packaging
- Baseline manifests taken after restore (`build_manifest`) for work/out.
- `write_dir_zip_to_uri(..., baseline=manifest)` includes only changed files.
- Implemented in `distributed_snapshot.py` and used in `py_code_exec_entry.py`.

### Bundle snapshot + caching
- Bundle version is computed from bundle content (SHA‑256 prefix) at load time in
  `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`.
- Snapshot path:
  `cb/tenants/<tenant>/projects/<project>/ai-bundle-snapshots/<bundle_id>.<version>.zip`
  and `.sha256`
- If version is missing, fallback uses SHA256 prefix as version.
- Cached if object already exists (no re-upload).
- Implemented in `ensure_bundle_snapshot` in `distributed_snapshot.py`.

### Bundle restore in exec
- `BUNDLE_SNAPSHOT_URI` is restored to `/workspace/bundles/<bundle_id>` in exec entrypoint.
- Runtime globals are rewritten so tool module paths point at restored bundle root.
- Implemented in `py_code_exec_entry.py` via `rewrite_runtime_globals_for_bundle`.

### Fargate runner
- `FARGATE_EXEC_ENABLED=1` enables execution.
- Required env:
  `FARGATE_CLUSTER`, `FARGATE_TASK_DEFINITION`, `FARGATE_CONTAINER_NAME`,
  `FARGATE_SUBNETS` (comma list), `FARGATE_SECURITY_GROUPS` (comma list),
  `FARGATE_ASSIGN_PUBLIC_IP`.
- Runner is implemented in `kdcube_ai_app/apps/chat/sdk/runtime/external/fargate.py`.

### Runtime-independent result contract

External execution does not change what the agent sees semantically.

Whether exec runs:

- in local isolated mode
- in Docker on the proc host
- or in distributed Fargate mode

the final user-facing tool result is still assembled in `exec_tools.py` after runtime completion.

The runtime contributes backend status fields such as:

- `ok`
- `returncode`
- `error`
- `error_summary`
- `stderr_tail`

Then `exec_tools.py` combines those with:

- contract file checks
- artifact validation
- `infra.log`
- `user.log`

and produces the final `report_text` that the agent reasons over.

### Output merge rules
- `logs/*` are appended into existing log files.
- `turn_*/*` outputs are copied into the live outdir.
- `timeline.json` and `sources_pool.json` are never overwritten.

### Exec context propagation
- `EXEC_CONTEXT` contains tenant/project/user/conversation/turn/session/run_id.
- Set in `kdcube_ai_app/apps/chat/sdk/runtime/execution.py`.
