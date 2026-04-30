---
id: ks:docs/exec/run-py-README.md
title: "Run Py"
summary: "Minimal setup to run Python via the ISO runtime in Docker (exec tools quickstart)."
tags: ["exec", "python", "iso-runtime", "quickstart", "docker"]
keywords: ["run python", "exec_tools", "ISO runtime", "docker executor", "bundle example", "out_dir"]
see_also:
  - ks:docs/exec/README-iso-runtime.md
  - ks:docs/exec/runtime-README.md
  - ks:docs/exec/operations.md
---
# Run Python in ISO Runtime (Docker) — Minimal Developer Guide

This guide explains the **minimal configuration** needed to run Python via the
ISO runtime in Docker, using the same execution layer the example bundle uses.
It focuses on **how to use the tools**, **which env vars to set**, and how
bundle tools are available inside the sandbox.

Related docs:
- [docs/exec/README-iso-runtime.md](README-iso-runtime.md) (architecture + supervisor/executor)
- [docs/exec/exec-logging-error-propagation-README.md](exec-logging-error-propagation-README.md) (log streams and error detection)
- [docs/exec/operations.md](operations.md) (deployment details)

---

## What you get

- Execute untrusted Python in a **network‑isolated executor**.
- Use **bundle tools** from the supervisor (network, KB, etc.).
- Receive artifacts + logs under the runtime `out/` tree.
- In split Docker mode, generated code sees only the artifact workspace as
  `OUTPUT_DIR=/workspace/out`; the proc-side full runtime tree keeps metadata,
  logs, and preserved programs separately.
- If the bundle uses prepared readonly local data, make that data physically available inside isolated exec too.

Current public React-facing tool surface:
1) **Contract-based** (`exec_tools.execute_code_python`)

Additional internal helpers exist for no-contract and side-effects execution, but they are not the normal public React tool surface.

---

## Minimal Docker Configuration

### Required env vars (chat service)

```
PY_CODE_EXEC_IMAGE=py-code-exec:latest
PY_CODE_EXEC_TIMEOUT=600
PY_CODE_EXEC_NETWORK_MODE=host
```

### Required mounts (Docker‑in‑Docker)

- `/var/run/docker.sock:/var/run/docker.sock`
- `HOST_EXEC_WORKSPACE_PATH` → `/exec-workspace`
- If bundle tools are used:
  - `HOST_BUNDLES_PATH` → `BUNDLES_ROOT`
- If bundle readonly local data is used:
  - `HOST_BUNDLE_STORAGE_PATH` → `BUNDLE_STORAGE_ROOT`

### Required paths

```
HOST_EXEC_WORKSPACE_PATH=/abs/path/to/exec-workspace
HOST_BUNDLES_PATH=/abs/path/to/bundles
BUNDLES_ROOT=/bundles
HOST_MANAGED_BUNDLES_PATH=/abs/path/to/managed-bundles
MANAGED_BUNDLES_ROOT=/managed-bundles
HOST_BUNDLE_STORAGE_PATH=/abs/path/to/bundle-storage
BUNDLE_STORAGE_ROOT=/bundle-storage
```

---

## Tool availability inside the sandbox

Tools defined in a bundle `tools_descriptor.py` are **available in the executor**:
- The supervisor loads tool modules using `RUNTIME_GLOBALS_JSON`.
- The executor calls them via:

```
await agent_io_tools.tool_call(
    fn=<tool_fn>,
    params={...},
    tool_id="alias.tool_name",
)
```

This means networked tools stay **in the supervisor**, while untrusted code
stays **in the executor**.

If a bundle also depends on prepared local readonly data, the runtime passes a concrete absolute path in:

```
BUNDLE_STORAGE_DIR
```

That path points at the per-bundle readonly storage dir physically available inside isolated exec.
Example: `kdcube.copilot` reads its built knowledge space from `BUNDLE_STORAGE_DIR`.

---

## Tool 1 — Contract-based execution

**Tool:** `exec_tools.execute_code_python`

**Behavior**
- You provide a **contract** listing expected output files.
- After execution, files are validated.
- Missing/invalid outputs are reported as errors.

**Contract item**
```
{"filename": "turn_<id>/files/...", "description": "..."}
```

**Example**
```python
await agent_io_tools.tool_call(
    fn=exec_tools.execute_code_python,
    params={
        "contract": [
            {
                "filename": "turn_123/files/report.md",
                "description": "Summary report"
            }
        ],
        "timeout_s": 300,
        "prog_name": "report_generator"
    },
    tool_id="exec_tools.execute_code_python",
)
```

---

## Internal side-effects mode

There is also an internal side-effects execution helper in `exec_tools.py`.
It can diff `out/` before and after the run and report created/modified files.

Important:

- this is not the normal public React-facing tool surface today
- the public documented path remains `exec_tools.execute_code_python(...)` with a non-empty file contract

## Logging and diagnostics

See [docs/exec/exec-logging-error-propagation-README.md](exec-logging-error-propagation-README.md).

Summary:
- `out/logs/user.log` or split-mode `out/logs/executor/user.log` — program stdout/stderr (+ logging if enabled)
- `out/logs/infra.log` — supervisor/executor runtime logs
- split mode stores supervisor logs under `out/logs/supervisor/` and executor
  logs under `out/logs/executor/`; the executor should only see
  `/workspace/logs/executor`
- `EXEC_USER_LOG_MODE` controls whether `logging.*` goes into user.log
- `EXEC_TRACEBACK_REMAP=0` disables line number remap for loader `main.py` frames

Execution layout:
- `work/main.py` is the stable loader executed by the runtime
- `work/user_code.py` is the verbatim program body generated by the agent / exec tool caller
- top-level `await` is supported in `user_code.py`
- when debugging preserved sources under `out/executed_programs/<execution_id>/`, inspect `user_code.py` first
- user artifacts are written under `OUTPUT_DIR`; in split Docker mode that is
  persisted under proc-side `out/workdir/`

---

## Runtime constraints

- Executor has **no network access**.
- The intended writable surfaces are `/workspace/work`, `OUTPUT_DIR`
  (`/workspace/out`), executor-local logs, and the supervisor socket mount.
- Generated file sizes are bounded by the ISO runtime. Defaults are `100MiB` for a single file and `250MiB` net new workspace bytes per run.
- Use `OUTPUT_DIR` and write to `turn_<id>/files/...`.
- Treat `BUNDLE_STORAGE_DIR` as readonly when it is present.

Filesystem limit controls are configured in `assembly.yaml`:

```yaml
platform:
  services:
    proc:
      exec:
        max_file_bytes: 100m
        max_workspace_bytes: 250m
        workspace_monitor_interval_s: 0.5
```

The runtime forwards these values into the isolated executor as internal
`EXEC_*` env values. Do not use those env names as the public configuration
surface.

The same values may be provided in an exec runtime profile as:
```yaml
max_file_bytes: 100m
max_workspace_bytes: 250m
workspace_monitor_interval_s: 0.5
```

---

## Side-effects vs contract selection
