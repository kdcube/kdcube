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
- Because there is no privileged supervisor boundary, descriptor-backed secrets and bundle props cannot be made executor-invisible in this mode.

**Docker (supervised sandbox)**  
- Supervisor executes tool calls; executor runs untrusted code with no network/keys.  
- Read‑only root FS; only workdir/outdir writable.  
- Stronger isolation and policy enforcement.
- Descriptor-backed platform config is restored only for the supervisor side; the executor child does not inherit descriptor payload env or descriptor path env.

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
- The local Docker launcher grants the supervisor container `SYS_ADMIN` and
  `NET_ADMIN` so `bubblewrap` can create mount namespaces and initialize the
  isolated no-network namespace for the executor child.
- The local Docker launcher uses `seccomp=unconfined` for the supervisor
  container because Docker's default seccomp profile blocks the `pivot_root`
  syscall used by `bubblewrap`. This applies to the trusted supervisor
  container; the untrusted executor child still runs inside the bwrap
  filesystem sandbox and no-network namespace.
- Only `/workspace/work` and `/workspace/out` are writable.
- `/tmp` is tmpfs.
- Bundle code roots are mounted read-only under a supervisor-only private root (if used).
- If a bundle exposes prepared readonly local data, its per-bundle storage dir is also mounted read-only under a supervisor-only private root.

```
Host
└─ /exec-workspace/react_<id>/
   ├─ work/   <----- bind mount to /workspace/work (RW)
   └─ out/    <----- bind mount to /workspace/out  (RW)

Host bundle surfaces (optional)
├─ /bundles/<bundle_id>/                 <----- bind mount to /tmp/kdcube-supervisor/bundles/<bundle_id> (RO)
└─ /bundle-storage/<tenant>/<project>/...<----- bind mount to /tmp/kdcube-supervisor/bundle-storage/... (RO)

Chat container (UID/GID 1000:1000)
└─ /exec-workspace/ (bind from host)

Exec container (entrypoint runs as root)
└─ /workspace/work (RW)
└─ /workspace/out  (RW)
└─ /tmp/kdcube-supervisor/... (RO, supervisor-only mounts for bundle code and bundle storage)
```

Important:

- bundle code and bundle readonly data are separate surfaces
- bundle code contains bundle-local tool modules, for example a path like `tools/react_tools.py` under the bundle root
- bundle readonly data contains prepared local assets such as built knowledge indexes or cloned docs repos
- in Docker supervised mode, those surfaces are mounted only for the supervisor side under `/tmp/kdcube-supervisor/...`
- generated code in the executor does not receive those mount paths in env or runtime globals

## Supervisor vs executor

The exec container runs two roles inside the same container:

1) **Supervisor process**
   - Bootstraps tool modules with full runtime globals.
   - Has network access.
   - Handles tool calls over a Unix socket.
   - Generates a per-execution supervisor auth token and requires it on every
     socket request before executing any tool.

2) **Executor subprocess**
   - Runs LLM-generated Python.
   - Has **no network** (network namespace unshared).
   - Runs inside the filesystem sandbox when `bubblewrap` is available.
   - Only sees the explicit runtime mounts needed to run Python plus `workdir`, `outdir`, the supervisor socket, and safe runtime dependencies.
   - Only writes to `workdir` and `outdir`.
   - Receives the per-execution supervisor auth token and delegates external
     actions to supervisor tools through the socket.

Descriptor-backed config behavior in supervised external runtimes:

- the proc exports `assembly.yaml`, `bundles.yaml`, `gateway.yaml`, `secrets.yaml`, and `bundles.secrets.yaml` as descriptor payload env values
- `py_code_exec_entry.py` materializes them into a root-only directory under `/tmp/kdcube-runtime-descriptors/<exec_id>`
- it then sets `PLATFORM_DESCRIPTORS_DIR`, `ASSEMBLY_YAML_DESCRIPTOR_PATH`, `BUNDLES_YAML_DESCRIPTOR_PATH`, `GATEWAY_YAML_PATH`, `GLOBAL_SECRETS_YAML`, and `BUNDLE_SECRETS_YAML` for the supervisor bootstrap
- the executor child does not inherit those env vars
- the materialized descriptor files are created with root-only permissions and are not mounted into the `bubblewrap` filesystem view

This is the current mechanism that allows supervisor-side tools to keep using `get_settings()`, `get_plain()`, and `get_secret()` when local deployment descriptors are the source of truth.

The executor is spawned by `_run_subprocess(...)` in
`kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py`.

When `bubblewrap` is available, isolation is implemented primarily by the
filesystem/network namespace: only explicit safe runtime paths, `workdir`,
`outdir`, and the supervisor socket are visible. The `bubblewrap` path uses a
user namespace and runs the executor child as UID `1001` / GID `1000`.
`/proc` is intentionally not mounted into the executor; only an empty `/proc`
directory is present so generated code cannot inspect container process/kernel
surfaces through procfs.

The supervisor socket authorization is based on the per-execution random token
generated by `py_code_exec_entry.py`. Linux `SO_PEERCRED` UID is logged as a
diagnostic signal, but it is not a reliable hard boundary when bwrap user
namespaces are used because the supervisor may observe the namespace-translated
peer as UID `0` while the executor sees itself as UID `1001`.

When `bubblewrap` is not available and filesystem sandboxing is not required,
the fallback subprocess path drops to:

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
      runtime.err.log
      docker.out.log
      docker.err.log
      user.log
      infra.log
```

Important:

- `py_code_exec.py` still executes `workdir/main.py`
- `main.py` is now a small loader owned by the platform
- the actual agent-written program body is `workdir/user_code.py`
- when debugging generated code, inspect `executed_programs/<execution_id>/user_code.py` first
- `runtime.err.log` is the inner executor process capture. It may contain both captured stdout and stderr sections for failed runs so the agent can see useful diagnostics even when the program printed the only clue to stdout.
- `docker.err.log` / `docker.out.log` are the outer `docker run` process logs.
- `infra.log` is synthesized later by diagnostics from the component logs.

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
- `HOST_MANAGED_BUNDLES_PATH`
  - Host path of the managed bundles root (mounted into chat container as `/managed-bundles`).
- `HOST_BUNDLE_STORAGE_PATH`
  - Host path of shared bundle storage root (used to translate proc-visible bundle storage paths into host-visible paths for Docker-in-Docker).
- `BUNDLES_ROOT`
  - Container path for non-managed local path bundles in the chat container.
- `MANAGED_BUNDLES_ROOT`
  - Container path for managed bundles in the chat container.
- `BUNDLE_STORAGE_ROOT`
  - Shared bundle storage root inside the chat container (typically `/bundle-storage`).

### Chat container runtime
- `PY_CODE_EXEC_IMAGE`
  - Docker image to run for ISO executor (`py-code-exec:latest` by default).
- `PY_CODE_EXEC_TIMEOUT`
  - Max runtime in seconds for isolated runs.
- `PY_CODE_EXEC_NETWORK_MODE`
  - Docker network mode for the exec container (`host` is typical).
- `platform.services.proc.exec.max_file_bytes`
  - Descriptor source for max single generated file size.
- `platform.services.proc.exec.max_workspace_bytes`
  - Descriptor source for max net-new workspace/output bytes per run.
- `platform.services.proc.exec.workspace_monitor_interval_s`
  - Descriptor source for workspace quota polling interval.

### Exec container runtime (set by chat runtime)
- `WORKDIR=/workspace/work`
- `OUTPUT_DIR=/workspace/out`
- `RUNTIME_GLOBALS_JSON`
  - Supervisor payload. The executor child receives a sanitized subset only.
- `RUNTIME_TOOL_MODULES`
  - List of tool module names to bind.
- `EXECUTION_ID`
  - Used for log headers and result file names.
- `BUNDLE_STORAGE_DIR`
  - Supervisor-only path for per-bundle readonly storage when the calling bundle needs prepared local data.
- `SUPERVISOR_SOCKET_PATH`
  - Unix socket between executor and supervisor (default `/tmp/supervisor.sock`).
- `LOG_DIR=/workspace/out/logs`
- `LOG_FILE_PREFIX=supervisor` (supervisor) or `executor` (executor)

## Security model summary

- **No network for executor**: unshared net namespace, only supervisor can call networked tools.
- **Read-only root FS**: the exec container root filesystem is read-only.
- **Filesystem sandbox for untrusted code**: the executor child is additionally narrowed with `bubblewrap` when available. The supervisor is not put inside bubblewrap because it must keep normal access to runtime services and tool modules.
- **Filesystem abuse limits**: the isolated entrypoint applies `RLIMIT_FSIZE` and a live workspace monitor so generated code cannot create oversized files or fill the writable workspace. Defaults are `100MiB` per file and `250MiB` net new bytes per run.
- **No secret env passthrough**: executor receives minimal, safe environment.
- **No descriptor or bundle-path globals for executor**: descriptor payload env, bundle root paths, bundle storage paths, and communicator bootstrap data stay out of executor globals/env.
- **Tool execution via supervisor**: network and external side effects are mediated.

## Filesystem limits

The ISO runtime enforces filesystem limits in the runtime boundary, not only in
React/tool contract validation. This applies to Docker and Fargate because both
paths enter the same `py_code_exec_entry.py -> run_py_code() -> _run_subprocess()`
executor path.

Descriptor controls:
- `platform.services.proc.exec.max_file_bytes`: maximum size for any generated single file. Default: `100m`. The value accepts raw bytes or suffixes such as `50m`, `100mb`, `1g`. Set to `0`, `disabled`, or `unlimited` only for trusted debugging.
- `platform.services.proc.exec.max_workspace_bytes`: maximum net new bytes across writable workdir/outdir for one run. Default: `250m`.
- `platform.services.proc.exec.workspace_monitor_interval_s`: live monitor interval. Default: `0.5`.

The proc runtime reads these values through `get_settings().PLATFORM.EXEC.PY`
and forwards them to the isolated process as internal `EXEC_*` env transport.
Do not configure them as platform env vars.

Bundles can override these limits for their own run in an exec runtime profile
using `max_file_bytes`, `max_workspace_bytes`, and
`workspace_monitor_interval_s`. If a limit is exceeded, the executor is
terminated and oversized generated files are removed where possible.

These size controls are separate from the broader information-disclosure
hardening work: they prevent resource exhaustion and oversized downloads, but
do not by themselves decide which internal paths the agent may inspect.

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
- `.../out/logs/user.log`, `.../out/logs/runtime.err.log`, and `.../out/logs/infra.log`
- `.../out/delta_aggregates.json` (supervisor deltas)
- `.../out/tool_calls_index.json` (tool call record)
