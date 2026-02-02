# Codegen Tool Roundtrip (codegen_tools.codegen_python)

This describes the end-to-end execution path for `codegen_tools.codegen_python`, including how artifacts are saved via `save_ret` and returned to ReAct.

## 1) ReAct decision -> execution
- ReAct decision selects `codegen_tools.codegen_python` with:
  - `instruction` (what to do)
  - `output_contract` (required artifacts)
- Execution path: `apps/chat/sdk/runtime/solution/react/execution.py`
  - `_execute_tool_in_memory(...)` detects `tools_insights.is_codegen_tool(tool_id)`
  - Calls `run_codegen_tool(...)`

## 2) run_codegen_tool -> CodegenRunner
File: `apps/chat/sdk/tools/codegen_tool.py`
- Builds a unique `result_filename` (e.g., `codegen_result_<id>.json`)
- Calls `codegen.run_as_a_tool(...)` with:
  - `program_playbook=context.operational_digest`
  - `output_contract`, `instruction`
  - `allowed_plugins`, `result_filename`, `outdir`, `workdir`

## 3) Isolated runtime entrypoint
Files:
- `apps/chat/sdk/runtime/isolated/py_code_exec_entry.py`
- `apps/chat/sdk/runtime/isolated/py_code_exec.py`

Flow:
- `py_code_exec_entry.py` prepares the run and calls `run_py_code(...)`
- `py_code_exec.py` injects a runtime header into `main.py` before execution

## 4) Header injection: step artifacts
File: `apps/chat/sdk/runtime/iso_runtime.py`
- Codegen tool uses `_build_iso_injected_header_step_artifacts(...)`
- The injected header defines:
  - `set_progress(...)`, `done()`, `fail(...)`
  - `_PROGRESS["out_dyn"]` as the artifact store
  - `result_filename` from env/args

## 5) Saving artifacts via save_ret
Inside the injected header:
- `set_progress(..., artifact=...)` updates `_PROGRESS["out_dyn"]`
- `done()` / `fail()` writes the final envelope
- All writes call:
  - `agent_io_tools.save_ret(data=<json>, filename=result_filename)`

This produces `OUTPUT_DIR/<result_filename>` inside the container.

Envelope includes:
- `ok`
- `contract`
- `out_dyn` (slot artifacts)
- `error` (if any)

## 6) Supervisor reads result
File: `apps/chat/sdk/tools/codegen_tool.py`
- Reads `OUTPUT_DIR/<result_filename>`
- Parses JSON payload
- Extracts slot artifacts:
  - `resource_id` is `slot:<slot_name>`
- Returns `artifacts` list in the envelope

## 7) React registers artifacts
File: `apps/chat/sdk/runtime/solution/react/execution.py`
- Iterates the returned `artifacts`
- Each artifact becomes a tool result item
- These are registered in `ReactContext` as current turn artifacts
- Slot mapping can happen in subsequent decisions

## Key invariants
- The codegen tool generates AND executes the program (no prewritten code input).
- Artifacts are persisted via `save_ret` from the injected header.
- Slot artifacts are identified by `resource_id="slot:<name>"` in the result envelope.
