# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── exec_contract.py ──
# Executes user code WITH an output contract (expected file list).
#
# An "output contract" is a list of files the code is expected to produce.
# After execution, the runtime checks which contracted files actually exist
# and reports succeeded/failed items.
#
# Under the hood, run_exec_tool() wraps the code in an async _main(),
# writes it as main.py, and passes it to _InProcessRuntime.execute_py_code().
# The runtime prepends an injected header (~900 lines) that sets up OUTPUT_DIR,
# logging, tool module imports, and signal handlers, then executes the script
# in a subprocess (with Linux namespace isolation) or Docker container.
# After execution it checks which contracted files exist and their sizes.
#
# Returns a tuple of (envelope, error, tool_params):
#   - envelope: execution result with succeeded/failed items (None on contract error)
#   - error:    contract validation error dict (None on success)
#   - tool_params: metadata about the execution for timeline/reporting

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    run_exec_tool,
    build_exec_output_contract,
)
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


async def run_with_contract(
    *,
    tool_manager: Any,
    logger: Optional[AgentLogger],
    contract_spec: List[Dict[str, Any]],
    code: str,
    timeout_s: int,
    workdir,
    outdir,
    exec_id: str,
    prog_name: str,
    exec_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, Any]]:
    # Step 1: validate and normalize the contract spec into internal format
    output_contract, normalized_artifacts, err = build_exec_output_contract(contract_spec)
    tool_params = {
        "contract": contract_spec,
        "timeout_s": timeout_s,
        "prog_name": prog_name,
    }
    if err:
        # Contract itself is invalid — return early without executing
        return None, err, tool_params

    # Step 2: run the code in the sandbox, checking output against the contract
    envelope = await run_exec_tool(
        tool_manager=tool_manager,
        logger=logger,
        output_contract=output_contract or {},
        code=code,
        contract=normalized_artifacts or [],
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        exec_id=exec_id,
        exec_runtime=exec_runtime,
    )
    return envelope, None, tool_params
