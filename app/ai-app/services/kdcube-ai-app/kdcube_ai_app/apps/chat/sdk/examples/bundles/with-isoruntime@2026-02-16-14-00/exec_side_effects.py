# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

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
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    envelope = await run_exec_tool_side_effects(
        tool_manager=tool_manager,
        logger=logger,
        code=code,
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        exec_id=exec_id,
    )
    diff = envelope.get("workspace_diff") or {}
    tool_params = {
        "timeout_s": timeout_s,
        "prog_name": prog_name,
        "mode": "side_effects",
    }
    return envelope, tool_params, diff
