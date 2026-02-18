# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

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
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, Any]]:
    output_contract, normalized_artifacts, err = build_exec_output_contract(contract_spec)
    tool_params = {
        "contract": contract_spec,
        "timeout_s": timeout_s,
        "prog_name": prog_name,
    }
    if err:
        return None, err, tool_params

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
    )
    return envelope, None, tool_params
