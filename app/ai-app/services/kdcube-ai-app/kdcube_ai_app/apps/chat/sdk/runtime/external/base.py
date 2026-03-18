# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import pathlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


@dataclass
class ExternalExecRequest:
    workdir: pathlib.Path
    outdir: pathlib.Path
    runtime_globals: Dict[str, Any]
    tool_module_names: List[str]
    timeout_s: int
    bundle_root: Optional[pathlib.Path] = None
    extra_env: Optional[Dict[str, str]] = None


@dataclass
class ExternalExecResult:
    ok: bool
    returncode: int
    error: Optional[str] = None
    seconds: Optional[float] = None


class ExternalRuntime:
    """Base interface for distributed execution runtimes."""

    async def run(self, request: ExternalExecRequest, *, logger: Optional[AgentLogger] = None) -> ExternalExecResult:
        raise NotImplementedError


def payload_size_bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def summarize_mapping_sizes(
    payload: Mapping[str, Any],
    *,
    top_n: int = 8,
) -> List[tuple[str, int]]:
    sized: list[tuple[str, int]] = []
    for key, value in payload.items():
        sized.append((str(key), payload_size_bytes(value)))
    sized.sort(key=lambda item: item[1], reverse=True)
    return sized[:top_n]


def format_size_summary(
    payload: Mapping[str, Any],
    *,
    top_n: int = 8,
) -> str:
    parts = [f"{key}={size}" for key, size in summarize_mapping_sizes(payload, top_n=top_n)]
    return ", ".join(parts) if parts else "<empty>"


def build_external_exec_env(
    *,
    base_env: Dict[str, Any],
    runtime_globals: Dict[str, Any] | None,
    tool_module_names: List[str] | None,
    exec_id: str,
    sandbox: str,
    log_file_prefix: str = "supervisor",
    bundle_root: Optional[str] = None,
    bundle_id: Optional[str] = None,
    include_runtime_payload: bool = True,
    extra_runtime_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    env = {str(k): str(v) for k, v in (base_env or {}).items()}
    env.update(
        {
            "WORKDIR": "/workspace/work",
            "OUTPUT_DIR": "/workspace/out",
            "LOG_DIR": "/workspace/out/logs",
            "LOG_FILE_PREFIX": log_file_prefix,
            "EXECUTION_ID": str(exec_id),
            "EXECUTION_SANDBOX": sandbox,
        }
    )
    if include_runtime_payload:
        env["RUNTIME_GLOBALS_JSON"] = json.dumps(runtime_globals or {}, ensure_ascii=False, default=str)
        env["RUNTIME_TOOL_MODULES"] = json.dumps(tool_module_names or [], ensure_ascii=False)
    if extra_runtime_env:
        env.update({str(k): str(v) for k, v in extra_runtime_env.items() if v is not None})
    if bundle_root:
        env["BUNDLE_ROOT"] = bundle_root
        env["EXEC_BUNDLE_ROOT"] = bundle_root
    if bundle_id:
        env["BUNDLE_ID"] = bundle_id
    return env
