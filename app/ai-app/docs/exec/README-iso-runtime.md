---
id: ks:docs/exec/README-iso-runtime.md
title: "ISO Runtime"
summary: "Design and operations of the ISO runtime: docker executor wiring, data paths, and isolation model."
tags: ["exec", "iso-runtime", "runtime", "docker", "architecture", "operations"]
keywords: ["ISO runtime", "executor container", "out_dir", "exec-workspace", "network isolation", "privilege separation"]
see_also:
  - ks:docs/exec/runtime-README.md
  - ks:docs/exec/run-py-README.md
  - ks:docs/exec/README-runtime-modes-builtin-tools.md
---
# Isolated Runtime (ISO) - Design and Operations

This document explains how isolated execution works in the chat runtime, when it is used, how the Docker executor is wired, and where data is stored.
It is aligned with `exec/operations.md` and the current implementation in:

- `kdcube_ai_app/apps/chat/sdk/solutions/react/v2/runtime.py`
- `kdcube_ai_app/apps/chat/sdk/runtime/execution.py`
- `kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py`
- `kdcube_ai_app/apps/chat/sdk/runtime/external/docker.py`
- `kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`
- `kdcube_ai_app/apps/chat/sdk/runtime/isolated/py_code_exec.py`

## Why ISO runtime exists

We execute untrusted, LLM-generated Python (codegen + exec tools). To keep the system safe:

- The **executor process** runs without network access.
- The container **root filesystem is read-only**.
- The executor can only write inside its `workdir` and `outdir`.
- In Docker mode, all **tool calls** are proxied via the supervisor. Any other code runs in the executor sandbox (no secrets, no network, write‑only to workdir/outdir).

## Local vs Docker (at a glance)

**Local (subprocess)**  
- Separate Python process on the same host.  
- No supervisor/executor split.  
- Crash containment only; no extra sandboxing beyond process boundary.

**Docker (supervised sandbox)**  
- Supervisor executes tool calls; executor runs untrusted code with no network/keys.  
- Read‑only root FS; only workdir/outdir writable.  
- Stronger isolation and policy enforcement.

## When ISO runtime is used

The React solver routes tool calls through `execute_tool` in
`kdcube_ai_app/apps/chat/sdk/runtime/execution.py`.

1) **Codegen tool** (`codegen_tools.codegen_python`)
   - Generates a stable `main.py` loader plus the verbatim generated `user_code.py`.
   - The isolated runtime executes `main.py`, which then runs `user_code.py`.
   - Output artifacts are written to the current React run outdir.

2) **Exec tool** (`exec_tools.execute_code_python`)
   - Writes the provided code verbatim to `user_code.py`.
   - The isolated runtime still enters through `main.py`, which loads and executes `user_code.py`.
   - Output artifacts and logs are written to the same outdir.

3) **Other tools**
   - `tools_insights.tool_isolation(...)` decides whether a tool runs:
     - in-memory (no isolation),
     - in local network isolation,
     - or in the Docker ISO executor.

## High-level flow

```
ReactSolver (chat container)
  └─ execute_tool(...)
       ├─ in-memory (safe tools only)
       └─ ISO runtime (exec/codegen and other isolated tools)
            └─ _InProcessRuntime.run_tool_in_isolation(...)
                 └─ docker.run_py_in_docker(...)
                      └─ py-code-exec container
                          ├─ supervisor (networked)
                          └─ executor (no network)
```

## Container topology and mounts

When isolation is required, `docker.run_py_in_docker(...)` starts a **py-code-exec** container:

- Root filesystem is read-only (`--read-only`).
- Only `/workspace/work` and `/workspace/out` are writable.
- `/tmp` is tmpfs.
- Bundle code roots are mounted read-only (if used).
- If a bundle exposes prepared readonly local data, its per-bundle storage dir is also mounted read-only.

```
Host
└─ /exec-workspace/react_<id>/
   ├─ work/   <----- bind mount to /workspace/work (RW)
   └─ out/    <----- bind mount to /workspace/out  (RW)

Host bundle surfaces (optional)
├─ /bundles/<bundle_id>/                 <----- bind mount to /bundles/<bundle_id> (RO)
└─ /bundle-storage/<tenant>/<project>/...<----- bind mount to that same absolute path in child (RO)

Chat container (UID/GID 1000:1000)
└─ /exec-workspace/ (bind from host)

Exec container (entrypoint runs as root)
└─ /workspace/work (RW)
└─ /workspace/out  (RW)
└─ /bundles/<bundle_id> (RO, if bundle-local tools are used)
└─ <BUNDLE_STORAGE_DIR> (RO, if bundle-local readonly data is used)
```

Important:

- bundle code and bundle readonly data are separate surfaces
- bundle code contains tool modules such as `tools/react_tools.py`
- bundle readonly data contains prepared local assets such as built knowledge indexes or cloned docs repos
- inside isolated exec, the physical readonly data path is exposed as `BUNDLE_STORAGE_DIR`

## Supervisor vs executor

The exec container runs two roles inside the same container:

1) **Supervisor process**
   - Bootstraps tool modules with full runtime globals.
   - Has network access.
   - Handles tool calls over a Unix socket.

2) **Executor subprocess**
   - Runs LLM-generated Python.
   - Has **no network** (network namespace unshared).
   - Only writes to `workdir` and `outdir`.
   - Delegates external actions to supervisor tools.

The executor is spawned by `_run_subprocess(...)` in
`kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py` and drops privileges:

- `EXECUTOR_UID` (default 1001)
- `EXECUTOR_GID` (default 1000)

## Storage layout (per React run)

Each React run creates a directory under `/exec-workspace`:

```
/exec-workspace/react_<id>/
  work/
    main.py                    # stable loader; injected and executed
    user_code.py               # verbatim generated/exec tool program
  out/
    timeline.json              # React timeline (written by chat)
    tool_calls_index.json      # tool call index (shared)
    exec_result_*.json         # exec tool outputs
    codegen_result_*.json      # codegen outputs
    delta_aggregates.json      # deltas from supervisor
    executed_programs/         # preserved loader + original user program, grouped per execution
      <execution_id>/
        main.py
        user_code.py
    logs/
      supervisor.log
      executor.log
      runtime.out.log
      runtime.err.log
      docker.out.log
      docker.err.log
      errors.log
```

Important:

- `py_code_exec.py` still executes `workdir/main.py`
- `main.py` is now a small loader owned by the platform
- the actual agent-written program body is `workdir/user_code.py`
- when debugging generated code, inspect `executed_programs/<execution_id>/user_code.py` first

## Permissions and ownership model

**Chat container user:**
- `appuser` (UID 1000, GID 1000)

**Exec container user:**
- Entrypoint starts as root, then drops to:
  - `EXECUTOR_UID` (default 1001)
  - `EXECUTOR_GID` (default 1000)

**Why this matters:**
- React (chat) writes `timeline.json`.
- Executor writes `exec_result_*.json`, logs, and tool outputs.
- Shared files must be **group-writable** by GID 1000.

**Enforced behavior:**
- `py_code_exec_entry.py` sets `umask(0o002)` to create group-writable files.
- `py_code_exec.py` chowns output dir to `1001:1000` and `chmod -R g+rwX`.
- The executor sets `umask(0o002)` in `_run_subprocess(...)`.

If permissions drift, fix on the host:
```
sudo chown -R ubuntu:ubuntu /path/to/exec-workspace
sudo chmod -R g+rwX /path/to/exec-workspace
```

## Environment variables

### Host / compose
- `HOST_EXEC_WORKSPACE_PATH`
  - Host path mounted to `/exec-workspace` in the chat container.
- `HOST_BUNDLES_PATH`
  - Host path of bundles root (mounted into chat container).
- `HOST_BUNDLE_STORAGE_PATH`
  - Host path of shared bundle storage root (used to translate proc-visible bundle storage paths into host-visible paths for Docker-in-Docker).
- `AGENTIC_BUNDLES_ROOT`
  - Container path for bundles in chat container.
- `BUNDLE_STORAGE_ROOT`
  - Shared bundle storage root inside the chat container (typically `/bundle-storage`).

### Chat container runtime
- `PY_CODE_EXEC_IMAGE`
  - Docker image to run for ISO executor (`py-code-exec:latest` by default).
- `PY_CODE_EXEC_TIMEOUT`
  - Max runtime in seconds for isolated runs.
- `PY_CODE_EXEC_NETWORK_MODE`
  - Docker network mode for the exec container (`host` is typical).

### Exec container runtime (set by chat runtime)
- `WORKDIR=/workspace/work`
- `OUTPUT_DIR=/workspace/out`
- `RUNTIME_GLOBALS_JSON`
  - Includes `PORTABLE_SPEC_JSON`, `TOOL_ALIAS_MAP`, `TOOL_MODULE_FILES`, `RAW_TOOL_SPECS`, etc.
- `RUNTIME_TOOL_MODULES`
  - List of tool module names to bind.
- `EXECUTION_ID`
  - Used for log headers and result file names.
- `BUNDLE_STORAGE_DIR`
  - Absolute per-bundle readonly storage dir available inside the isolated runtime when the calling bundle needs prepared local data.
- `SUPERVISOR_SOCKET_PATH`
  - Unix socket between executor and supervisor (default `/tmp/supervisor.sock`).
- `LOG_DIR=/workspace/out/logs`
- `LOG_FILE_PREFIX=supervisor` (supervisor) or `executor` (executor)

## Security model summary

- **No network for executor**: unshared net namespace, only supervisor can call networked tools.
- **Read-only root FS**: only `/workspace/work` and `/workspace/out` are writable.
- **No secret env passthrough**: executor receives minimal, safe environment.
- **Tool execution via supervisor**: network and external side effects are mediated.

## Parallel execution considerations (future)

Shared outdir is safe only if there is **one active executor per React run**.
If you run multiple execs in parallel that write to the same outdir:

- `timeline.json` and `tool_calls_index.json` can race.
- Result files should be uniquely named (`exec_result_<id>.json` already is).

If parallel execs are added later:
1) Add file locks around `timeline.json` and `tool_calls_index.json` writes.
2) Ensure each exec uses a unique `EXECUTION_ID`.
3) Consider per-exec subdirectories under `out/` if logs need isolation.

## Where to look for troubleshooting

- `exec/operations.md`
- `.../out/logs/errors.log` and `.../out/logs/runtime.err.log`
- `.../out/delta_aggregates.json` (supervisor deltas)
- `.../out/tool_calls_index.json` (tool call record)
