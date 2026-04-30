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
- In Docker mode, all **tool calls** are proxied via the supervisor. Any other code runs in the executor sandbox (no secrets, no network, filesystem access limited to the mounted work/artifact/log surfaces).

## Local vs Docker (at a glance)

**Local (subprocess)**  
- Separate Python process on the same host.  
- No supervisor/executor split.  
- Crash containment only; no extra sandboxing beyond process boundary.
- Because there is no privileged supervisor boundary, descriptor-backed secrets and bundle props cannot be made executor-invisible in this mode.

**Docker (supervised sandbox)**  
- Supervisor executes tool calls; executor runs untrusted code with no network/keys.  
- Read‑only root FS; only the configured work/artifact/log mounts are writable.
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

When isolation is required, `docker.run_py_in_docker(...)` starts the
**py-code-exec** runtime using one of two container strategies:

- `combined`: one container contains both the trusted supervisor and the
  generated-code executor subprocess. The executor child has no network and
  drops to the executor uid/gid before user code starts.
- `split`: the proc starts two sibling containers. The supervisor container
  receives descriptor-backed configuration, bundle/tool mounts, network, and
  runtime storage. The executor container receives only the work mount, artifact
  output mount, executor log mount, supervisor socket, and a minimal safe env.

Common properties:

- Root filesystem is read-only (`--read-only`).
- `/tmp` is tmpfs.
- Bundle code roots are mounted read-only only where the supervisor needs them.
- Bundle readonly storage is mounted read-only only where the supervisor needs it.
- Generated code writes artifacts under `OUTPUT_DIR`; in split mode this maps
  to the artifact workspace, not to the full runtime output tree.

```
Host, combined strategy
└─ /exec-workspace/react_<id>/
   ├─ work/   <----- bind mount to /workspace/work (RW)
   └─ out/    <----- bind mount to /workspace/out  (RW)

Host bundle surfaces (optional)
├─ /bundles/<bundle_id>/                 <----- bind mount to /tmp/kdcube-supervisor/bundles/<bundle_id> (RO)
└─ /bundle-storage/<tenant>/<project>/...<----- bind mount to /tmp/kdcube-supervisor/bundle-storage/... (RO)

Chat container (UID/GID 1000:1000)
└─ /exec-workspace/ (bind from host)

Exec container in `combined` strategy (entrypoint runs as root)
└─ /workspace/work (RW)
└─ /workspace/out  (RW)
└─ /tmp/kdcube-supervisor/... (RO, supervisor-only mounts for bundle code and bundle storage)

Host, split strategy
└─ /exec-workspace/react_<id>/
   ├─ work/                  <----- supervisor + executor /workspace/work (RW)
   └─ out/                   <----- supervisor /workspace/runtime-out (RW)
      ├─ workdir/            <----- executor /workspace/out (RW artifacts only)
      └─ logs/
         ├─ supervisor/      <----- supervisor LOG_DIR
         └─ executor/        <----- executor /workspace/logs/executor

Executor container in `split` strategy
└─ /workspace/work           (RW)
└─ /workspace/out            (RW artifact root; host out/workdir)
└─ /workspace/logs/executor  (RW executor logs only)
└─ /supervisor-socket        (RW socket volume)

Supervisor container in `split` strategy
└─ /workspace/work           (RW)
└─ /workspace/out            (RW artifact root; host out/workdir)
└─ /workspace/runtime-out    (RW full runtime output tree)
└─ /tmp/kdcube-supervisor/... (private supervisor runtime roots)
```

Important:

- bundle code and bundle readonly data are separate surfaces
- bundle code contains bundle-local tool modules, for example a path like `tools/react_tools.py` under the bundle root
- bundle readonly data contains prepared local assets such as built knowledge indexes or cloned docs repos
- in Docker `combined` mode, those surfaces are mounted only for the supervisor side under `/tmp/kdcube-supervisor/...`
- in Docker `split` mode, those surfaces are mounted only into the supervisor container and are not mounted into the executor container
- generated code in the executor does not receive those mount paths in env or runtime globals

## Supervisor vs executor

In `combined` strategy, the exec container runs two roles inside the same
container. In `split` strategy, the same roles run in sibling containers:

1) **Supervisor process**
   - Bootstraps tool modules with full runtime globals.
   - Has network access.
   - Handles tool calls over a Unix socket.
   - Generates a per-execution supervisor auth token and requires it on every
     socket request before executing any tool.

2) **Executor subprocess**
   - Runs LLM-generated Python.
   - Has **no network** (network namespace unshared).
   - Drops to `EXECUTOR_UID` (default `1001`) / `EXECUTOR_GID` (default `1000`) before user code starts.
   - Receives only a minimal safe environment, plus `workdir`, `outdir`, and the supervisor socket.
   - Only writes to `workdir` and `outdir`.
   - Receives the per-execution supervisor auth token and delegates external
     actions to supervisor tools through the socket.

Descriptor-backed config behavior in supervised external runtimes:

- the proc exports `assembly.yaml`, `bundles.yaml`, `gateway.yaml`, `secrets.yaml`, and `bundles.secrets.yaml` as descriptor payload env values
- `py_code_exec_entry.py` materializes them into a root-only directory under `/tmp/kdcube-runtime-descriptors/<exec_id>`
- it then sets `PLATFORM_DESCRIPTORS_DIR`, `ASSEMBLY_YAML_DESCRIPTOR_PATH`, `BUNDLES_YAML_DESCRIPTOR_PATH`, `GATEWAY_YAML_PATH`, `GLOBAL_SECRETS_YAML`, and `BUNDLE_SECRETS_YAML` for the supervisor bootstrap
- the executor child does not inherit those env vars
- the materialized descriptor files are created with root-only permissions and are not passed to generated code

This is the current mechanism that allows supervisor-side tools to keep using `get_settings()`, `get_plain()`, and `get_secret()` when local deployment descriptors are the source of truth.

The executor is spawned by `_run_subprocess(...)` in
`kdcube_ai_app/apps/chat/sdk/runtime/iso_runtime.py`.

The subprocess path creates a private network namespace, then drops to:

- `EXECUTOR_UID` (default 1001)
- `EXECUTOR_GID` (default 1000)

Before dropping uid/gid, the executor child clears inherited supplementary
groups with `setgroups([EXECUTOR_GID])`. The generated-code process should not
retain supplementary group `0(root)`.

Supervisor socket authorization uses both Linux `SO_PEERCRED` UID checks and a
per-execution random token generated by `py_code_exec_entry.py`.

## Container Strategies

Docker ISO runtime supports two strategies:

- `combined` (default): supervisor and generated-code executor run in one
  `py-code-exec` container. Generated code still runs as a no-network
  UID-dropped child process. This preserves the historical behavior.
- `split`: the proc starts two sibling containers. The supervisor container
  receives descriptors, bundle code, network, and tool modules. The executor
  container receives only the current `/workspace/work`, `/workspace/out`, a
  shared supervisor socket, a minimal safe env, and stripped runtime globals.
  The executor container runs with `--network none`, `--read-only`,
  `--cap-drop=ALL`, a small capability add-back only for ownership/UID drop,
  and `no-new-privileges`.

Configure platform default:

```yaml
platform:
  services:
    proc:
      exec:
        py_code_exec_container_strategy: "split"  # combined | split
```

Per-bundle override:

```yaml
config:
  execution:
    runtime:
      mode: "docker"
      container_strategy: "split"
```

Use `split` when generated code must not be able to read supervisor-side
descriptors, bundle storage, platform storage, or other runtime internals by
filesystem access. Tool calls still go through the supervisor and must remain
allowlisted there.

## Storage layout (per React run)

Each React run creates a directory under `/exec-workspace`. The proc/runtime
side keeps a full diagnostic tree:

```
/exec-workspace/react_<id>/
  work/
    main.py                    # stable loader; injected and executed
    user_code.py               # verbatim generated/exec tool program
  out/
    timeline.json              # React timeline (written by chat)
    tool_calls_index.json      # tool call index (shared)
    delta_aggregates.json      # deltas from supervisor
    executed_programs/         # preserved loader + original user program, grouped per execution
      <execution_id>/
        main.py
        user_code.py
    logs/
      docker.out.log
      docker.err.log
      infra.log
      supervisor/
        supervisor.log
      executor/
        executor.log
        runtime.err.log
        user.log
    workdir/                   # artifact workspace exposed as OUTPUT_DIR to generated code
      exec_result_*.json
      codegen_result_*.json
      turn_<id>/
        files/
        outputs/
        attachments/
```

Important:

- `py_code_exec.py` still executes `workdir/main.py`
- `main.py` is now a small loader owned by the platform
- the actual agent-written program body is `workdir/user_code.py`
- when debugging generated code, inspect `out/executed_programs/<execution_id>/user_code.py` first
- `OUTPUT_DIR` points at the artifact workspace. In split Docker mode that is
  `/workspace/out` inside the executor and `out/workdir` on the proc/host side.
- runtime metadata such as `timeline.json`, `delta_aggregates.json`,
  `executed_programs/`, supervisor logs, docker logs, and `infra.log` stay
  outside the executor-visible artifact workspace in split mode.
- `logs/executor/runtime.err.log` is the inner executor process capture. It may
  contain both captured stdout and stderr sections for failed runs so the agent
  can see useful diagnostics even when the program printed the only clue to stdout.
- `logs/docker.err.log` / `logs/docker.out.log` are the outer Docker launcher logs.
- `infra.log` is synthesized later by diagnostics from the component logs.

When the workspace is persisted to `ConversationStore`, the platform keeps one
compact archive per execution context. The archive is named after the context
id and contains both the runtime output tree and the executed package tree:

```text
executions/<user>/<conversation>/<turn>/<ctx_id>/
  <ctx_id>.zip
    out/
      timeline.json
      logs/
      workdir/
      executed_programs/
    pkg/
      main.py
      user_code.py
```

Keeping one blob avoids slow object-store uploads and large expanded trees. For
local `file://` storage, unpack `<ctx_id>.zip` when deep inspection is needed.

## Permissions and ownership model

**Chat container user:**
- `appuser` (UID 1000, GID 1000)

**Exec container user:**
- Entrypoint starts as root, then drops to:
  - `EXECUTOR_UID` (default 1001)
  - `EXECUTOR_GID` (default 1000)

**Why this matters:**
- React (chat) writes `timeline.json` and runtime metadata.
- Executor writes `exec_result_*.json`, executor logs, and tool outputs.
- Shared files must be **group-writable** by GID 1000.

**Enforced behavior:**
- `py_code_exec_entry.py` sets `umask(0o002)` to create group-writable files.
- `py_code_exec.py` chowns output dir to `1001:1000` and `chmod -R g+rwX`.
- The executor clears supplementary groups, sets `umask(0o002)`, then drops to
  `EXECUTOR_GID` / `EXECUTOR_UID` in `_run_subprocess(...)`.

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
  - Generated-code artifact root. In split mode this maps to host/proc
    `out/workdir`, not to the full runtime output tree.
- `KDCUBE_ARTIFACT_OUTPUT_DIR=/workspace/out`
  - Explicit artifact root alias.
- `KDCUBE_RUNTIME_OUTPUT_DIR=/workspace/runtime-out`
  - Supervisor/runtime-only full output tree in split mode. Not passed to the
    generated-code executor.
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
- `LOG_DIR`
  - Combined/local/Fargate: usually `/workspace/out/logs`.
  - Split supervisor: `/workspace/runtime-out/logs/supervisor`.
  - Split executor: `/workspace/logs/executor`.
- `EXECUTOR_LOG_DIR=/workspace/logs/executor`
  - Split executor log root.
- `LOG_FILE_PREFIX=supervisor` (supervisor) or `executor` (executor)

## Security model summary

- **No network for executor**: unshared net namespace, only supervisor can call networked tools.
- **Read-only root FS**: the exec container root filesystem is read-only.
- **Narrow writable surface for untrusted code**: `/workspace/work`,
  artifact `OUTPUT_DIR`, executor-local logs, and the supervisor socket mount
  are the only intended writable surfaces. The Docker root filesystem is read-only.
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
- `.../out/logs/executor/user.log`, `.../out/logs/executor/runtime.err.log`, and `.../out/logs/infra.log`
- `.../out/logs/supervisor/supervisor.log`
- `.../out/logs/docker.err.log` / `.../out/logs/docker.out.log`
- `.../out/delta_aggregates.json` (supervisor deltas)
- `.../out/tool_calls_index.json` (tool call record)
