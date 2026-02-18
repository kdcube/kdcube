# Run Python in ISO Runtime (Docker) — Minimal Developer Guide

This guide explains the **minimal configuration** needed to run Python via the
ISO runtime in Docker, using the same execution layer the example bundle uses.
It focuses on **how to use the tools**, **which env vars to set**, and how
bundle tools are available inside the sandbox.

Related docs:
- `docs/exec/README-iso-runtime.md` (architecture + supervisor/executor)
- `docs/exec/logging-README.md` (log streams and error detection)
- `docs/exec/operations.md` (deployment details)

---

## What you get

- Execute untrusted Python in a **network‑isolated executor**.
- Use **bundle tools** from the supervisor (network, KB, etc.).
- Receive artifacts + logs in `out/`.

Two execution modes:
1) **Contract-based** (`exec_tools.execute_code_python`)
2) **Side‑effects** (`exec_tools.execute_code_python_side_effect`)  
   (illustrative; may be commented out to avoid agent selection)

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
  - `HOST_BUNDLES_PATH` → `AGENTIC_BUNDLES_ROOT`

### Required paths

```
HOST_EXEC_WORKSPACE_PATH=/abs/path/to/exec-workspace
HOST_BUNDLES_PATH=/abs/path/to/bundles
AGENTIC_BUNDLES_ROOT=/bundles
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

## Tool 2 — Side-effects execution

**Tool:** `exec_tools.execute_code_python_side_effect`  
(illustrative; can be commented out in tools registry)

**Behavior**
- No contract is provided.
- The runtime diffs `out/` before and after the run.
- Created/modified files become artifacts.
- Deleted files are reported as notices.

**Example**
```python
await agent_io_tools.tool_call(
    fn=exec_tools.execute_code_python_side_effect,
    params={
        "timeout_s": 300,
        "prog_name": "side_effects_run"
    },
    tool_id="exec_tools.execute_code_python_side_effect",
)
```

---

## Logging and diagnostics

See `docs/exec/logging-README.md`.

Summary:
- `out/logs/user.log` — program stdout/stderr (+ logging if enabled)
- `out/logs/infra.log` — supervisor/executor runtime logs
- `EXEC_USER_LOG_MODE` controls whether `logging.*` goes into user.log
- `EXEC_TRACEBACK_REMAP=0` disables line number remap in `main.py` tracebacks

---

## Runtime constraints

- Executor has **no network access**.
- Only `/workspace/work` and `/workspace/out` are writable.
- Use `OUTPUT_DIR` and write to `turn_<id>/files/...`.

---

## Side-effects vs contract selection

