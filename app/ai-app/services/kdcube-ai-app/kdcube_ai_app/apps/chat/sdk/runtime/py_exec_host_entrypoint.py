# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/exec_host_entrypoint.py

import asyncio
import json
import os
import pathlib

from kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime import run_py_code
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

async def _main():
    workdir = pathlib.Path(os.environ.get("WORKDIR") or "/workspace")
    outdir = pathlib.Path(os.environ.get("OUTPUT_DIR") or "/output")

    raw_globals = os.environ.get("RUNTIME_GLOBALS_JSON") or "{}"
    try:
        runtime_globals = json.loads(raw_globals)
    except Exception:
        runtime_globals = {}

    logger = AgentLogger("py_code_exec")

    await run_py_code(
        workdir=workdir,
        output_dir=outdir,
        globals=runtime_globals,
        logger=logger,
        timeout_s=int(os.environ.get("PY_CODE_EXEC_TIMEOUT", "600")),
    )

if __name__ == "__main__":
    asyncio.run(_main())
