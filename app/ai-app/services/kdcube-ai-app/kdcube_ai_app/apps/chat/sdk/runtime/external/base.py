# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

