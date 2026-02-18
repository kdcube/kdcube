# with-isoruntime bundle (minimal iso-runtime harness)

This bundle is a **minimal iso-runtime harness** that runs a hardcoded Python snippet
inside the docker sandbox **without ReAct**. It is meant to isolate and validate:
- the execution stack
- tool wiring
- workspace sync behavior
- diagnostics/logging surface

## What it does
1. Creates a `ToolSubsystem` from `tools_descriptor.py`.
2. Bootstraps a **per-user sandbox** from a **per-user workspace**.
3. Executes a scenario-specific Python snippet.
   - Contract mode: `exec_tools.run_exec_tool(...)`
   - Side-effects mode (no contract): `exec_tools.run_exec_tool_no_contract(...)`
4. Syncs **only `out/`** back to the user workspace (overwrite).
5. Emits an execution report (tree + log tails) and raises `comm.error` if
   `user.log` contains `ERROR` lines or a traceback.
6. Writes a note using a **bundle-local tool** (`local_tools.write_note`).

## Workspace / sandbox layout
```
examples/bundles/data/
  workspace/<user-id>/    # persistent user workspace
  sandbox/<user-id>/      # per-run sandbox (overwritten each run)
```

Inside the sandbox, the exec runtime uses:
```
sandbox/<user-id>/
  work/   # runtime workdir (main.py etc.)
  out/    # runtime outdir (artifacts, logs)
```
Only `out/` is synced back to the user workspace.

## Env vars
Override paths if needed:
- `ISO_RUNTIME_USER_WORKSPACE_ROOT`
  - default: `.../examples/bundles/data/workspace`
- `ISO_RUNTIME_SANDBOX_ROOT`
  - default: `.../examples/bundles/data/sandbox`

## Tools
This bundle registers one bundle-local tool:
```
tools/local_tools.py  -> alias: local_tools
```

It exposes:
```
local_tools.write_note(text: str)
```
which writes: `OUTPUT_DIR/notes/<timestamp>-note.txt`

## Scenarios (clickable in UI)
The workflow selects a scenario from the user input (e.g., `0.` or `scenario 3`).
Each scenario is listed in the UI as a suggested followup.

Scenario **0** is the happy path. It writes:
- `turn_<id>/files/hello-iso-runtime.txt`
and then calls:
```
await agent_io_tools.tool_call(
    fn=local_tools.write_note,
    params={"text": "note from iso-runtime"},
    call_reason="Write a simple note file",
    tool_id="local_tools.write_note",
)
```

Other scenarios simulate timeouts, crashes, partial output, etc. See `exec.py`.

### Contract vs side-effects
- **Contract mode** (default): the contract defines expected outputs; missing/invalid
  files are reported as errors.
- **Side-effects mode**: no contract is enforced; we diff `out/` before/after and
  report created/modified/deleted files.

Implementation modules:
- `exec_contract.py` — contract execution path
- `exec_side_effects.py` — side-effects execution path (diffs `out/`)

## Docker note
If running in Docker/DinD, ensure the sandbox root is mounted into the exec
container. The simplest option is to point `ISO_RUNTIME_SANDBOX_ROOT` at a
mounted exec-workspace path.

## Execution diagnostics
After each exec run, the workflow collects:
- A tree of the sandbox root (excluding `logs/`).
- Tail of `out/logs/user.log` (program output only).
- Tail of `out/logs/infra.log` (merged infra logs).
- Extracted `ERROR` lines from the program log.

If `user.log` contains `ERROR` lines or a traceback, the workflow emits:
```
self.comm.error(message="Program error detected in user.log", ...)
```

## Program logging
Use the dedicated program logger to write into `user.log`:
```
import logging
log = logging.getLogger("user")
log.info("hello from program")
```
