# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── exec_side_effects.py ──
# Executes user code WITHOUT an output contract (side-effects mode).
#
# Unlike exec_contract.py, this mode does NOT declare expected output files.
# Instead, it snapshots the output directory before execution, runs the code
# via _InProcessRuntime (subprocess / Docker — same isolation as contract mode),
# then diffs outdir before/after to discover created/modified files.
# Useful when the output set is unpredictable.
#
# Returns a tuple of (envelope, tool_params, diff):
#   - envelope:    execution result
#   - tool_params: metadata for timeline/reporting
#   - diff:        workspace_diff showing which files changed

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple, Optional

from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import run_exec_tool_side_effects
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


async def run_without_contract(
    *,
    tool_manager: Any,
    logger: Optional[AgentLogger],
    code: str,
    timeout_s: int,
    workdir: Path,
    outdir: Path,
    exec_id: str,
    prog_name: str,
    exec_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    # Run code and capture workspace changes via before/after diff
    envelope = await run_exec_tool_side_effects(
        tool_manager=tool_manager,
        logger=logger,
        code=code,
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        exec_id=exec_id,
        exec_runtime=exec_runtime,
    )
    # Extract the diff of created/modified files in the output directory
    diff = envelope.get("workspace_diff") or {}
    tool_params = {
        "timeout_s": timeout_s,
        "prog_name": prog_name,
        "mode": "side_effects",
    }
    return envelope, tool_params, diff
