---
id: repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
title: "ISO Runtime"
summary: "Design and operations of the ISO runtime: docker executor wiring, data paths, and isolation model."
tags: ["exec", "iso-runtime", "runtime", "docker", "architecture", "operations"]
keywords: ["ISO runtime", "executor container", "out_dir", "exec-workspace", "network isolation", "privilege separation"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/run-py-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-runtime-modes-builtin-tools.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-recording-event-sinks-README.md
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

## Exec docs map

- This file is the main entry point for the supervisor/executor isolation model.
- [runtime-README.md](runtime-README.md) is the architecture reference with detailed diagrams.
- [distributed-exec-README.md](distributed-exec-README.md) covers Fargate and remote snapshot transport.
- [operations.md](operations.md) is the deployment and troubleshooting guide.
- [run-py-README.md](run-py-README.md) is the minimal developer quickstart.
- [README-runtime-modes-builtin-tools.md](README-runtime-modes-builtin-tools.md) explains tool runtime selection.
- [exec-logging-error-propagation-README.md](exec-logging-error-propagation-README.md) explains log streams and error surfacing.

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

### Host path hints vs executor isolation

In Docker-in-Docker deployments, `chat-proc` runs inside a container but starts
sibling Docker containers through the host Docker daemon. Paths visible inside
`chat-proc` are therefore not automatically valid as Docker bind-mount source
paths on the host. The runtime uses host path hints such as:

```yaml
paths:
  host_exec_workspace_path: "/opt/kdcube/efs/exec-workspace"
```

or the equivalent environment variable:

```bash
HOST_EXEC_WORKSPACE_PATH=/opt/kdcube/efs/exec-workspace
```

to translate proc-visible paths like:

```text
/exec-workspace/react_<id>/out/logs/executor
```

into Docker-daemon-visible host paths like:

```text
/opt/kdcube/efs/exec-workspace/react_<id>/out/logs/executor
```

This translation does not grant the executor access to the full host workspace.
It only tells Docker where the source directory for a specific narrow bind mount
lives. In split mode, the executor container still receives only:

```text
/workspace/work
/workspace/out
/workspace/logs/executor
/supervisor-socket
```

The executor must not receive `/workspace/runtime-out`, `logs/supervisor`,
`infra.log`, bundle roots, bundle storage, platform storage, descriptor files,
or provider secrets.

Operationally, this distinction matters:

- ECS/EC2 deployments normally set `HOST_EXEC_WORKSPACE_PATH` to an EFS-backed
  host path, for example `/opt/kdcube/efs/exec-workspace`. The EFS access point
  creates the workspace with the expected service UID/GID, so Docker bind mounts
  existing writable directories.
- Local or EC2 CLI deployments that omit `host_exec_workspace_path` may fall back
  to host `/exec-workspace`. If that path does not exist, Docker may create a
  root-owned bind source, which can later make the UID-dropped executor fail when
  opening `/workspace/work/main.py` or `/workspace/logs/executor/executor.log`.

The runtime should defensively pre-create and chmod split bind-source surfaces
through the proc-visible `/exec-workspace` tree before launching sibling
containers. This includes the executor-visible surfaces (`work`, artifact
output, and `logs/executor`) plus supervisor-owned runtime/log paths needed by
the supervisor container. Pre-creating a supervisor-owned path is not a grant to
the executor: the executor argv must still exclude `/workspace/runtime-out`,
`logs/supervisor`, `infra.log`, bundle roots, bundle storage, platform storage,
descriptor files, and provider secrets.

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

Split strategy container views:

```text
Supervisor container                         Executor container
────────────────────                         ──────────────────
/workspace/work/                             /workspace/work/
  main.py                                      main.py
  user_code.py                                user_code.py

/workspace/out/                              /workspace/out/
  exec_result_*.json                           exec_result_*.json
  codegen_result_*.json                        codegen_result_*.json
  turn_<id>/                                   turn_<id>/
    files/                                       files/
    outputs/                                     outputs/
    attachments/                                 attachments/

/workspace/runtime-out/                      /workspace/logs/executor/
  timeline.json                                executor.log
  tool_calls_index.json                        runtime.err.log
  delta_aggregates.json                        user.log
  comm_recorded_events.json
  executed_programs/
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
  workdir/                                   /supervisor-socket
    ... same artifact tree as /workspace/out

/tmp/kdcube-supervisor/
  bundles/<bundle_id>/        # RO bundle code
  bundle-storage/...          # RO prepared bundle data
```

The split executor view intentionally excludes the full runtime output tree,
supervisor logs, `infra.log`, descriptor files, bundle roots, bundle storage,
platform storage, and provider secrets. If an attachment is only present as
hosted metadata, it is not in this tree. It becomes part of the executor-visible
workspace only after the platform materializes or pulls it into
`turn_<id>/attachments/`.

The supervisor has the broader runtime view because it owns tool execution,
network calls, bundle-local tools, and diagnostics. Generated code cannot read
those supervisor-only paths directly; it must ask the supervisor through the
socket and the supervisor applies the tool allowlist and runtime policy.

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

- Proc does not promote provider API keys such as `BRAVE_API_KEY` into generated-code env. It packages the same deployment descriptor inputs the host uses.
- The Docker/Fargate supervisor receives descriptor payload env values:
  - `KDCUBE_RUNTIME_ASSEMBLY_YAML_B64` for `assembly.yaml`
  - `KDCUBE_RUNTIME_BUNDLES_YAML_B64` for `bundles.yaml`
  - `KDCUBE_RUNTIME_GATEWAY_YAML_B64` for `gateway.yaml`
  - `KDCUBE_RUNTIME_SECRETS_YAML_B64` for `secrets.yaml`
  - `KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64` for `bundles.secrets.yaml`
- `py_code_exec_entry.py` materializes them into a root-only directory under `/tmp/kdcube-runtime-descriptors/<exec_id>`.
- It then sets `PLATFORM_DESCRIPTORS_DIR`, `ASSEMBLY_YAML_DESCRIPTOR_PATH`, `BUNDLES_YAML_DESCRIPTOR_PATH`, `GATEWAY_YAML_PATH`, `GLOBAL_SECRETS_YAML`, and `BUNDLE_SECRETS_YAML` for the supervisor bootstrap and clears the settings cache.
- Supervisor-side tools use the normal config APIs: `get_settings()`, `get_plain()`, and `get_secret()` / `get_secret()`.
- Bundle props come from `bundles.yaml` bundle `config` (or legacy `props`) and are exposed to bundle tools through `bundle_props` / `ScopedBundleConfig`.
- Bundle secrets come from `bundles.secrets.yaml` or the configured secrets provider. A bundle-scoped lookup such as `b:docs.api_key` resolves under the active bundle id.
- By default, descriptor payloads preserve the full platform descriptor files for the trusted supervisor. To scope bundle descriptors to the caller bundle, set `execution.runtime.descriptor_payload_scope: active_bundle` in bundle runtime config. In that mode only `bundles.yaml` and `bundles.secrets.yaml` are filtered; `assembly.yaml`, `gateway.yaml`, and global `secrets.yaml` stay unchanged.
- The executor child does not inherit descriptor payload env vars, descriptor path env vars, or secret-provider material.
- The materialized descriptor files are created with root-only permissions and are not passed to generated code.

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
  `--cap-drop=ALL`, narrow capability add-backs for bind-source ownership,
  UID/GID drop, and entrypoint lifecycle control, and `no-new-privileges`.
  `CAP_KILL` is present only so the root entrypoint can terminate its own
  UID-dropped generated-code child on timeout, cancellation, or quota breach.
  The generated-code child drops to UID/GID 1001/1000, verifies it has zero
  effective Linux capabilities, installs an inherited seccomp deny rule for
  `socket(AF_ALG, ...)`, and the executor image removes setuid/setgid bits from
  base-image helper binaries.

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
    comm_recorded_events.json  # recorded comm events from supervisor/runtime
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
  `comm_recorded_events.json`, `executed_programs/`, supervisor logs, docker
  logs, and `infra.log` stay outside the executor-visible artifact workspace in
  split mode.
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

## Comm State Side-File Handoff

Mechanically, today:

1. Host serializes comm into `COMM_SPEC`.
2. If `comm.record(...)` was enabled, `_export_comm_spec_for_runtime()` adds:

```python
"recording": {
    "enabled": True,
    "filter": <serializable selector>,
    "max_events": ...
}
```

3. Iso runtime rebuilds comm from `COMM_SPEC`.
   It calls:

```python
comm.record(recording["filter"], mode="replace", max_events=...)
```

So yes, the iso comm knows whether to record and which events through serialized comm data. Only serializable selector filters are propagated; callable filters are not.

4. During iso execution, comm records in memory.
5. Supervisor/runtime writes side files:
   - `delta_aggregates.json`
   - `comm_recorded_events.json`

6. Host always attempts merge after isolated execution returns:

```python
tool_manager.comm.merge_delta_cache_from_file(outdir / "delta_aggregates.json")
tool_manager.comm.merge_recorded_events_from_file(outdir / "comm_recorded_events.json")
```

On cancellation: it is **best effort**, not guaranteed. We write on normal `done()`, managed `fail()`, supervisor shutdown path, and SIGTERM/SIGINT/atexit best-effort paths. If the runtime is hard-killed, the node dies, output dir is lost, or the process gets no cleanup window, the files may not exist and host merge is a no-op.

So the correct doc statement should be: graceful/managed cancellation should usually preserve already dumped/flushable comm state; hard cancellation does not guarantee it.

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
- `platform.services.proc.exec.max_exec_workspace_delta_bytes`
  - Descriptor source for max net-new monitored writable bytes per exec call.
- `platform.services.proc.exec.max_workspace_bytes`
  - Optional descriptor source for max total bytes in the active workspace writable roots before finalization/offload.
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
- **Filesystem abuse limits**: the isolated entrypoint applies `RLIMIT_FSIZE` and a live workspace monitor so generated code cannot create oversized files or fill the writable workspace. Defaults are `100MiB` per file and `250MiB` net new bytes per exec call.
- **Capability boundary**: the split executor container starts with
  `--cap-drop=ALL` and only narrow add-backs needed by the root entrypoint.
  Generated code runs after UID/GID drop and must have zero effective Linux
  capabilities.
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
- `platform.services.proc.exec.max_exec_workspace_delta_bytes`: maximum net-new bytes across monitored writable roots for one exec call. Default: `250m`.
- `platform.services.proc.exec.max_workspace_bytes`: optional maximum total bytes currently present in the active workspace writable roots before finalization/offload. This catches accumulation across multiple exec calls in the same active workspace.
- `platform.services.proc.exec.workspace_monitor_interval_s`: live monitor interval. Default: `0.5`.

The monitored writable roots are the executor work directory, artifact
`OUTPUT_DIR`, `LOG_DIR`, and `EXECUTOR_LOG_DIR`. Current-turn artifacts created
by the assistant under `files/` or `outputs/` count toward these limits. User
attachments also count when they are materialized into the active turn artifact
workspace, for example under `turn_<id>/attachments/`, or when the agent pulls
an attachment into the workspace before using it. Attachments that only exist in
hosted storage or timeline metadata are not counted until they become local
files in one of the monitored roots.

The proc runtime reads these values through `get_settings().PLATFORM.EXEC.PY`
and forwards them to the isolated process as internal `EXEC_*` env transport.
Do not configure them as platform env vars.

Bundles can override these limits for their own run in an exec runtime profile
using `max_file_bytes`, `max_exec_workspace_delta_bytes`, `max_workspace_bytes`, and
`workspace_monitor_interval_s`. If the active workspace is already over the
configured total limit before an exec starts, generated code is not launched. If
a limit is detected during or after execution, the result is marked as an error
while any successfully produced contracted artifacts remain available to the
caller.

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
- `.../out/comm_recorded_events.json` (recorded comm events)
- `.../out/tool_calls_index.json` (tool call record)
